#!/usr/bin/python3
# coding=utf-8

# Copyright (c) 2019 github0null@outlook.com
# This program is free software: you can redistribute it and/or modify it 
# under the terms of the GNU General Public License as published by the Free Software Foundation, 
# either version 3 of the License, or (at your option) any later version.
# This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; 
# without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. 
# See the GNU General Public License for more details.
# You should have received a copy of the GNU General Public License along with this program. 
# If not, see <https://www.gnu.org/licenses/>.

import sys
import os
import re
import json
import subprocess
import click
from datetime import datetime
import time
import copy

import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('[r730-fanctl]')

# 参考: https://www.dell.com/support/manuals/zh-cn/poweredge-r730/r730_ompublication
# ---
# 1.标准操作温度范围：
#   在设备无直接光照的情况下，10 °C 至 35 °C（50 °F 至 95 °F）。
STD_OPT_TEMP = 35 # degree centigrade
# ---
# 2.扩展操作温度范围：
#   相对湿度 (RH) 为 5% 至 85%，工作温度为 5°C 至 40°C，露点为 29°C。
# ---
# 3.扩展操作温度限制：
#   请勿在 5°C 以下执行冷启动。
#   指定的操作温度适用的最高海拔高度为 3050 米（10,000 英尺）。
#   不支持 160 W 或更高功率的处理器。
#   需要冗余电源设备。
#   不支持非 Dell 认证的外围设备卡和/或超过 25 W 的外围设备卡。
#   3.5 英寸硬盘驱动器机箱支持最大 120 W 处理器。
#   2.5 英寸硬盘驱动器机箱支持最大 145 W 处理器。
#   3.5 英寸硬盘驱动器机箱背部的硬盘驱动器插槽中仅允许 SSD。
#   不支持中端驱动器配置、八个 3.5 英寸以及十八个 1.8 英寸 SSD 配置。
#   不支持 GPU
#   不支持磁带备份单元 (TBU)。
EXT_OPT_TEMP = 40 # degree centigrade

# ===== utility funcs =====

def get_script_root():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    elif __file__:
        return os.path.dirname(__file__)
    else:
        raise Exception('error !, can not get script root !')

def to_abs_path(repath: str):
    if not os.path.isabs(repath):
        return os.path.normpath(get_script_root() + os.path.sep + repath)
    else:
        return repath

def exec_cmd(cmdline: str, encoding='utf8', no_split=False):
    proc = subprocess.Popen(args=cmdline, 
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True,
        shell=True, cwd=get_script_root(), encoding=encoding)
    stdout, stderr = proc.communicate()
    if proc.returncode != 0:
        raise Exception('Fail to exec: "{}", stderr: {}'.format(cmdline, stderr))
    if no_split:
        return stdout
    return stdout.splitlines()

# ===== entry =====

IDRAC_HOST = os.environ.get('IDRAC_HOST', None)
IDRAC_USER = os.environ.get('IDRAC_USER', 'root')
IDRAC_PASS = os.environ.get('IDRAC_PASS', 'calvin')
FANCTL_MIN_SPD = int(os.environ.get('FANCTL_MIN_SPD', '10'))

SENSOR_STATUS = {
    'Inlet': None,
    'Exhaust': None,
    'CPU': None,
    'GPU': None,
    'DISK': None,
    'Power': None,
    'FAN': None
}

FAN_SPEED_MAP = {
    # FAN MAP
    'CPU': [
        (35, FANCTL_MIN_SPD),
        (40, 12),
        (45, 12),
        (50, 18),
        (55, 24),
        (60, 30),
        (65, 40),
        (70, 50),
        (75, 65),
        (80, 80),
        (85, 90),
    ],
    # ALL GAINS
    'GAIN_GPU': [
        (60, 2),
        (65, 5),
        (70, 10),
        (75, 10),
        (80, 15),
        (85, 20),
    ],
    'GAIN_Inlet': [
        (25, 1),
        (32, 1.2),
        (STD_OPT_TEMP, 1.3),
        (EXT_OPT_TEMP, 1.5)
    ],
    'GAIN_DISK': [
        (48, 1),
        (50, 3),
        (53, 5),
        (56, 8),
        (59, 10),
        (65, 15)
    ]
}

ADJ_PERIOD   = 30
FAN_CUR_PWM  = 0
SPD_HOLD_CNT = 0

# Query
#  ipmitool -I lanplus -H 192.168.0.105 -U root -P calvin sensor list
#  ipmitool -I lanplus -H 192.168.0.105 -U root -P calvin sdr type Fan
#  ipmitool -I lanplus -H 192.168.0.105 -U root -P calvin sdr type Temperature
#  ipmitool -I lanplus -H 192.168.0.105 -U root -P calvin sdr type Current
# Fan ctrl
#  ipmitool -I lanplus -H 192.168.0.105 -U root -P calvin raw 0x30 0x30 0x01 0x01      --- Auto
#  ipmitool -I lanplus -H 192.168.0.105 -U root -P calvin raw 0x30 0x30 0x01 0x00      --- Manual
#  ipmitool -I lanplus -H 192.168.0.105 -U root -P calvin raw 0x30 0x30 0x02 0xff 0x0f --- Ctrl Fan PWM
def ipmitool(cmd):
    global IDRAC_HOST, IDRAC_USER, IDRAC_PASS
    return exec_cmd(f'ipmitool -I lanplus -H {IDRAC_HOST} -U {IDRAC_USER} -P {IDRAC_PASS} {cmd}')

def poll_sensor():
    global SENSOR_STATUS

    # clear all
    for k in SENSOR_STATUS:
        SENSOR_STATUS[k] = None

    # get temp by impi
    #  Inlet Temp       | 04h | ok  |  7.1 | 20 degrees C
    #  Exhaust Temp     | 01h | ok  |  7.1 | 31 degrees C
    lines = ipmitool(f'sdr type Temperature')
    for line in lines:
        m = re.search(r'([-\d]+) degrees', line)
        if m:
            if 'Inlet Temp' in line:
                SENSOR_STATUS['Inlet'] = int(m[1])
            elif 'Exhaust Temp' in line:
                SENSOR_STATUS['Exhaust'] = int(m[1])

    # get CPU temp
    try:
        # cat /sys/class/thermal/thermal_zone0/type -> x86_pkg_temp
        # cat /sys/class/thermal/thermal_zone0/temp -> 47000
        # 对x86架构的CPU，type应为x86_pkg_temp；而arm架构的CPU，type为CPU-therm
        for n in os.listdir('/sys/class/thermal'):
            if n.startswith('thermal_zone'):
                r = exec_cmd(f'cat /sys/class/thermal/{n}/type', no_split=True).strip().lower()
                if r.startswith('x86_pkg') or r.startswith('cpu-'):
                    s = exec_cmd(f'cat /sys/class/thermal/{n}/temp', no_split=True).strip()
                    t = int(s) / 1000
                    if SENSOR_STATUS['CPU'] != None:
                        SENSOR_STATUS['CPU'] = max(t, SENSOR_STATUS['CPU'])
                    else:
                        SENSOR_STATUS['CPU'] = t
    except Exception as err:
        SENSOR_STATUS['CPU'] = None
        logger.warning(f'skip CPU temp. {err}')

    # get DISK temp, make sure the following pkg installed:
    #  `sudo apt install hddtemp`
    try:
        # root:~/projects/hddtemp-0.3-beta15# inxi -xD
        # Drives:
        #     Local Storage: total: 4.09 TiB used: 22.77 GiB (0.5%)
        #     ID-1: /dev/nvme0n1 vendor: SanDisk model: SSD Plus 500GB A3N size: 465.76 GiB temp: 40.9 C
        for line in exec_cmd('inxi -c 0 -xD'):
            m = re.search(r'temp\: ([\-\d\.]+) C', line)
            if m:
                logger.debug(f'match line: {line}')
                n = float(m[1])
                o = SENSOR_STATUS['DISK']
                if o == None or n > o:
                    SENSOR_STATUS['DISK'] = n
    except Exception as err:
        SENSOR_STATUS['DISK'] = None
        logger.warning(f'skip DISK temp. {err}.')

    # nvidia-smi -q -d TEMPERATURE
    # GPU Current Temp : 32 C
    try:
        for line in exec_cmd('nvidia-smi -q -d TEMPERATURE'):
            m = re.search(r'GPU Current Temp\s*\:\s*([\-\d\.]+) C', line)
            if m:
                logger.debug(f'match line: {line}')
                n = float(m[1])
                o = SENSOR_STATUS['GPU']
                if o == None or n > o:
                    SENSOR_STATUS['GPU'] = n
    except Exception as err:
        SENSOR_STATUS['GPU'] = None
        logger.warning(f'skip GPU temp. {err}')

    # Fan
    try:
        SENSOR_STATUS['FAN'] = poll_fan_spd()
    except Exception as err:
        SENSOR_STATUS['FAN'] = None
        logger.warning(f'skip Fan SPD. {err}')

    # Power
    try:
        SENSOR_STATUS['Power'] = poll_pwr_consume()
    except Exception as err:
        SENSOR_STATUS['Power'] = None
        logger.warning(f'skip Power. {err}')

    # dump
    logger.info(f'sensor status update at: {timestamp()}')
    for k in SENSOR_STATUS:
        unit_ = ''
        if k in 'Inlet Exhaust CPU GPU DISK': unit_ = '°C'
        if k in 'Power': unit_ = 'W'
        if k in 'FAN': unit_ = 'RPM'
        logger.info(f' - "{k}"\t: {str(SENSOR_STATUS[k])} {unit_}')

def poll_pwr_consume():
    lines = ipmitool(f'sdr type Current')
    for line in lines:
        m = re.search(r'(\d+) Watts', line)
        if m:
            return int(m[1])
    raise Exception('Fail to parse pwr_consume')

def poll_fan_spd():
    spds = []
    lines = ipmitool(f'sdr type Fan')
    for line in lines:
        m = re.search(r'(\d+) RPM', line)
        if m:
            spds.append(int(m[1]))
    if len(spds) == 0:
        raise Exception('sdr type Fan invalid response')
    return sum(spds) / len(spds)

# Fan SPD-FREQ:
# 线性区：每 5% 对应 约800RPM
#  25% -> 5600RPM
#  20% -> 4900RPM
#  15% -> 4100RPM
#  10% -> 3300RPM
#   5% -> 2600RPM
def rpm2pwm(rpm):
    pwm = 5 + (((rpm - 2600) / 800) * 5)
    if pwm > 100: pwm = 100
    return pwm

def fan_speed_ctrl(speed):
    global FAN_CUR_PWM
    speed = int(speed)
    if speed < 0: speed = 0
    if speed > 255: speed = 255
    FAN_CUR_PWM = speed
    ipmitool(f'raw 0x30 0x30 0x02 0xff {hex(speed)}')

def get_cpu_usage() -> float:
    r = exec_cmd('top -b -n1 | grep "Cpu(s)"', no_split=True)
    m = re.search(r'([\d\.]+) id', r)
    if m: return 100 - float(m[1])
    return 0

def compute_fan_output() -> int:
    base_spd = FANCTL_MIN_SPD
    for kv in FAN_SPEED_MAP['CPU']:
        if SENSOR_STATUS['CPU'] >= kv[0]:
            base_spd = kv[1]
    # Inlet gain
    inlet_gain = 1
    for kv in FAN_SPEED_MAP['GAIN_Inlet']:
        if SENSOR_STATUS['Inlet'] >= kv[0]:
            inlet_gain = kv[1]
    # [optional] gpu gain
    gpu_gain = 0
    if SENSOR_STATUS['GPU'] != None:
        for kv in FAN_SPEED_MAP['GAIN_GPU']:
            if SENSOR_STATUS['GPU'] >= kv[0]:
                gpu_gain = kv[1]
    # [optional] disk gain
    disk_gain = 0
    if SENSOR_STATUS['DISK'] != None:
        for kv in FAN_SPEED_MAP['GAIN_DISK']:
            if SENSOR_STATUS['DISK'] >= kv[0]:
                disk_gain = kv[1]
    # make result
    next_speed = base_spd + gpu_gain + disk_gain
    next_speed *= inlet_gain
    logger.debug(f'compute fan speed: {int(next_speed)} = (base:{base_spd} + gpu:{gpu_gain} + disk:{disk_gain}) * {inlet_gain}')
    return int(next_speed)

def adjust():
    global SPD_HOLD_CNT
    pwm = compute_fan_output()
    if pwm > FAN_CUR_PWM:
        if FAN_CUR_PWM == 0:
            logger.info(f"✔ setup fan init speed -> {pwm} %")
        else:
            logger.info(f"↗ fan speed up -> {pwm} %")
        fan_speed_ctrl(pwm)
        SPD_HOLD_CNT = 0
    elif pwm < FAN_CUR_PWM:
        SPD_HOLD_CNT += 1
        if get_cpu_usage() < 50 and SPD_HOLD_CNT >= (90 / ADJ_PERIOD):
            SPD_HOLD_CNT = 0
            logger.info(f"↘ fan speed down -> {pwm} %")
            fan_speed_ctrl(pwm)
        else:
            logger.info(f"fan speed no change, pwm: {FAN_CUR_PWM} %, waiting system low load ...")
    else:
        logger.info(f"fan speed no change, pwm: {FAN_CUR_PWM} %")

def giveup():
    try:
        logger.warning(f"Give control to iDRAC hardware.")
        ipmitool(f'raw 0x30 0x30 0x01 0x01')
        logger.info('Ok.')
    except Exception as err:
        logger.error(f'Fail to call ipmitool(): {str(err)}. Retry after 5 secs ...', err)
        time.sleep(5)
        ipmitool(f'raw 0x30 0x30 0x01 0x01')
        logger.info('Ok.')

def timestamp():
    return datetime.now().strftime("%Y/%m/%d %H:%M:%S")

@click.command()
@click.option('--host', default=IDRAC_HOST, type=click.STRING, help='the IP of your IDRAC. if no set, auto detect')
@click.option('--user', '-u', default=IDRAC_USER, type=click.STRING, help='your IPMI username')
@click.option('--passwd', '-p', default=IDRAC_PASS, type=click.STRING, help='your IPMI password')
@click.option('--min-speed', default=FANCTL_MIN_SPD, type=click.INT, help='fan min speed (%)')
@click.option('--set-pwm', default=None, type=click.INT, help='manual set fan pwm once')
@click.option('--fallback', default=False, type=click.BOOL, help='fallback to iDRAC auto control fan speed')
def main(host: str, user: str, passwd: str, min_speed: int, set_pwm: int, fallback: bool):
    global IDRAC_HOST, IDRAC_USER, IDRAC_PASS, FANCTL_MIN_SPD
    logger.info(f'====== process startup: {timestamp()} ======')
    # setup host
    if host == None:
        # auto detect HOST
        # root:~# ip route show
        #  169.254.0.0/24 dev idrac proto kernel scope link src 169.254.0.2 metric 100
        try:
            for line in exec_cmd('ip route show'):
                if 'dev idrac' in line:
                    m = re.search(r'link src (\d+\.\d+\.\d+\.\d+)', line)
                    if m:
                        IDRAC_HOST = m[1]
                        logger.info(f'Found idrac host: {IDRAC_HOST}')
                        break
            else:
                time.sleep(3)
                raise Exception('Not found idrac host. please use --host option')
        except:
            msg = 'No found command: "ip", maybe you need: "apt-get install iproute2"'
            logger.error(msg)
            time.sleep(3)
            raise Exception('Not found idrac host.')
    else:
        IDRAC_HOST = host
    # dump info
    if user: IDRAC_USER = user
    if passwd: IDRAC_PASS = passwd
    if min_speed != None: FANCTL_MIN_SPD = min_speed
    logger.info(f'IDRAC_HOST : {IDRAC_HOST}')
    logger.info(f'IDRAC_USER : {IDRAC_USER}')
    logger.info(f'IDRAC_PASS : {IDRAC_PASS[0] + "*" * (len(IDRAC_PASS) - 1)}')
    logger.info(f'MIN SPEED  : {FANCTL_MIN_SPD} %')
    # is fallback ?
    if fallback:
        giveup()
        return 0
    # init
    ipmitool(f'raw 0x30 0x30 0x01 0x00') # close idrac auto ctrl
    time.sleep(0.5)
    ipmitool(f'raw 0x30 0x30 0x01 0x00')
    time.sleep(0.5)
    # set pwm once ?
    if set_pwm != None:
        if set_pwm > 100: set_pwm = 100
        if set_pwm < 5: set_pwm = 5
        fan_speed_ctrl(set_pwm)
        logger.info(f'manual set fan speed once -> {set_pwm} %')
        return 0
    # check tools
    try:
        exec_cmd('inxi --version')
    except:
        logger.warning('Not found "inxi", maybe you need "apt-get install inxi"')
    # go loop
    retry_cnt = 0
    while True:
        try:
            poll_sensor()
            adjust()
            retry_cnt = 0
            time.sleep(ADJ_PERIOD)
        except Exception as err:
            retry_cnt += 1
            if retry_cnt > 3:
                retry_cnt = 0
                giveup()
                time.sleep(60) # delay and exit.
                break
            else:
                logger.error(f'Adjust error: {str(err)}. retry ...', err)
                time.sleep(3)
    logger.warning('process exit. reason: "Cannot auto adjust, give control to iDRAC hardware."')
    giveup()

if __name__ == '__main__':
    try:
        main()
        sys.exit(-1)
    except Exception as err:
        logger.error(f'Fatal error: {str(err)}. process exit.')
        sys.exit(-1)
