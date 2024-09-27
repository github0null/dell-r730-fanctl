# 戴尔R730风扇自动调速器

## 介绍

该脚本通过执行 ipmitool 命令，查询CPU温度，进而实现自动控制风扇转速，

支持根据 进风口温度，显卡温度，磁盘温度 对风扇转速进行自动补偿，

通过注册到系统服务，可以做到开机自启，完成自动接管

## 安装要求

1. Linux 系统
2. 安装 python3
3. 安装 python3 包：`click`
4. 脚本需要直接运行在 R730 主机上，需要root权限
5. 需要预先安装 `ipmitool`, `inxi` 工具

## 安装及使用

> 以 `debian` 系统为例

### 硬件准备

1. 准备一个路由器(路由器至少要有2个LAN口的)
2. 将你的主机背面的 IDRAC口 和 网口1（网口2，3也行）都连到路由器网口上，确保他俩处于同一网段
3. 启动主机，在路由器设置里，将IDRAC口的 IP 和 MAC 绑定，做成静态的。（这样做后面即使断电了IP也不会变）
4. 浏览器输入iDRAC的ip地址，进入管理界面，进入 "IDRAC设置->网络->IPMI"，将 "启用LAN上的IPMI" 打勾，通讯权限选择 "管理员" 

### 软件

1. 安装 ipmitool，执行 `apt ipmitool`
2. 安装 inxi，执行 `apt inxi`
3. 下载该仓库的 main.py 到你的主机上，保存到家目录下（本例：/root/dell-fanctl）
4. 在 `/etc/systemd/system` 目录下创建一个 `fanctl.service` 文件，文件内容为：
   ```
   [Unit]
   Description=dell r730 fanctrl
   After=network.target
   [Service]
   User=root
   RemainAfterExit=no
   Environment="IDRAC_HOST=192.168.0.105" # 填你的iDRAC网口的ip地址
   Environment="IDRAC_USER=root"          # 填你的iDRAC登录名，默认：root
   Environment="IDRAC_PASS=calvin"        # 此处填你的iDRAC密码，默认：calvin
   ExecStart=/root/dell-fanctl/main.py    # 这个路径要填成你下载下来的 main.py 的路径
   WorkingDirectory=/tmp
   Restart=on-abnormal
   RestartSec=5s
   KillMode=mixed
   StandardOutput=journal
   StandardError=journal
   [Install]
   WantedBy=multi-user.target
   ```

5. 检查上面新建的 `fanctl.service` 文件里的注释，将 HOST, USER, PASS 都设置好，保存文件

### 启动

1. 执行 `systemctl restart fanctl` 启动服务，如果提示无法找到服务，可以执行一下：`systemctl daemon-reload`

3. 执行 `systemctl status fanctl` 查看服务状态

   如果有如下日志，则已正常启动
   
   ```
   ● fanctl.service - dell r730 fanctrl
     Loaded: loaded (/etc/systemd/system/fanctl.service; enabled; preset: enabled)
     Active: active (running) since Fri 2024-09-27 17:19:07 CST; 9s ago
   Main PID: 78880 (main.py)
      Tasks: 3 (limit: 38390)
     Memory: 17.2M
        CPU: 1.965s
     CGroup: /system.slice/fanctl.service
   
    Sep 27 17:19:07 r730-cl systemd[1]: Started fanctl.service - dell r730 fanctrl.
    Sep 27 17:19:07 r730-cl main.py[78880]: INFO:[r730-fanctl]:====== process startup: 2024/09/27 17:19:07 ======
    Sep 27 17:19:07 r730-cl main.py[78880]: INFO:[r730-fanctl]:IDRAC_HOST : 192.168.0.105
    Sep 27 17:19:07 r730-cl main.py[78880]: INFO:[r730-fanctl]:IDRAC_USER : root
    Sep 27 17:19:07 r730-cl main.py[78880]: INFO:[r730-fanctl]:IDRAC_PASS : c*****
    Sep 27 17:19:07 r730-cl main.py[78880]: INFO:[r730-fanctl]:MIN SPEED  : 10 %
   ```

   接下来可以运行一些负载较高的程序，测试一下调速是否正常，比如 UnixBench

3. 检查调速功能无误后，执行 `systemctl enable fanctl` 将该服务设置为开机自启
