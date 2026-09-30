# TSL Router

树莓派 ARM64 / Docker 的 TSL UMD 5.0 UDP 接收、监看、映射和手动发送控制台。Python 标准库实现，无 pip 或前端 CDN 依赖。

## 部署

```sh
docker compose up -d --build
docker compose logs --tail 50
```

网页默认 `http://10.10.10.26:8080`，有线侧 `http://192.168.10.2:8080`。
初始接收 `0.0.0.0:40003/UDP`；固定从 `eth0 / 192.168.10.2` 发送。初始无映射，不主动向目标发送。

网页“网络设置”可选择接收网口（Wi-Fi、板载以太网、USB 以太网或全部）、接收端口（1–65535）及发送网口。本机源 IPv4 自动从所选网口获取，不应填写目标设备地址。接口改变即时生效，无需重启。每条映射的“编辑 → 输出目标”可独立设置目标 IPv4 和 UDP 端口。USB 网卡须已由系统配置好 IP；选网口不改变系统网络配置。拔插后重新打开设置可更新网口列表。

有线 SSH：电脑设为同网段的空闲 IP，例如 `192.168.10.3/24` 后运行 `ssh pi@192.168.10.2`。也可继续使用 Wi-Fi 地址登录。无需在容器内另开 SSH。

## 使用

1. 发送设备指向树莓派所选接收网口的 IP 及接收端口。支持多来源 IP；不要求来源 UDP 端口固定。
2. 输入卡片显示来源、Screen、Display ID、左/文字/右灯、亮度和最近收到时间。点击卡片可预填映射。
3. 映射按来源 IP + Screen + ID 匹配，支持 `*`。设置目标 IP、UDP 端口、Screen、ID；默认目标 `192.168.10.151:40003`。输出三个灯可分别选输入任一灯态或固定颜色。空白标签表示沿用输入。
4. “逐灯发送去向”可将每个输入/手动灯勾选发送到目标左灯、右灯、文字中的一个或多个位置。每个目标位置只使用一个来源，改选会替换旧来源；高级选项可固定颜色。自动与手动使用相同映射。自动模式将新输入转发；设置间隔可重复发送，0 表示仅收到时发送。超时仅标记输入过期，继续按间隔重发最后灯态；收到灭灯报文才发送灭灯。
5. 选择颜色和文字，点击“接管并发送”进入手动模式，外部输入继续显示但不覆盖此输出。“接管灭灯”发送三个灯全灭。“恢复自动”等待下一条新输入。“停用”不再发包，目标保持最后状态。
6. 全局暂停只暂停自动转发。手动控制仍可使用。修改不相关映射保留其他输出的重复发送。

配置保存到宿主机 `./data/config.json`（容器 `/data/config.json`），采用原子写入，保留上一版本 `config.json.bak`。容器重启后保留端口、网口、模式和映射；不回放缓存输入或自动启动旧的手动发送，需新输入或再次点击发送。容器以 `restart: always` 随 Docker 自动启动。

## 网页修改网口 IP

打开“网络设置 → 修改网口 IP”，选择实际网口，设置静态 IP/前缀、可选网关和 DNS，或使用 DHCP。此操作修改树莓派自身网络，而非 TSL 目标地址。应用后到新地址的 `:8080` 页面点击“确认保留”；120 秒内未确认会恢复旧配置。DHCP 地址请从路由器查询。未确认时重启也会尝试恢复原配置。

网口配置由宿主机 NetworkManager 持久保存。受限助手只接受网口 IPv4 配置请求，不暴露 shell 或 Docker socket。首次安装助手：

```sh
sudo install -d -m 755 /usr/local/lib/tsl-router
sudo install -m 644 network_helper.py /usr/local/lib/tsl-router/network_helper.py
sudo install -m 644 tsl-network.service /etc/systemd/system/tsl-network.service
sudo systemctl daemon-reload
sudo systemctl enable --now tsl-network.service
```

助手通过 `/run/tsl-network/control.sock` 与容器通信，恢复记录保存在 `/var/lib/tsl-network`。更新助手文件后需要重启 `tsl-network` 服务。网口尚未出现时网页仍可访问，程序会重试监听；发送源 IP 随网口地址更新。

## 范围和约束

- IPv4 UDP，单包上限 2048 字节，支持多 Display、ASCII、UTF-16LE 和 65535 Screen/ID 广播匹配。广播输入按各条规则转成指定输出 ID；设置输出 65535 时将广播给对应目标屏幕/显示器。
- 多个来源匹配同一条通配规则时，最后到达的输入生效；需要固定来源时填写具体 IP。禁止两个启用规则占用相同目标四元组。
- TSL 5.0 未定义的 Screen/Display 控制数据拒绝处理，不猜测其格式；不支持 TSL 3.1 或 TCP 包装。
- Linux host 网络 + NET_RAW 用于 SO_BINDTODEVICE；NET_BIND_SERVICE 支持低位接收端口。输出绑定指定接口，避免通过 Wi-Fi 默认路由发送。网口改址通过宿主机受限助手执行，容器没有 NET_ADMIN 或 privileged 权限。
- 页面无登录，限受信任局域网使用；不应直接映射到公网。JSON 写接口校验浏览器 Origin。
- UDP 发送成功不证明目标已收取；可通过目标灯态或抓包验证。无自动灭灯策略，以免未经配置改变现场状态。

协议依据：[TSL 官方 UMD 协议，第 6–7 页](https://tslproducts.com/wp-content/uploads/Manuals/Control/tsl-umd-protocol.pdf)。

## 验证

```sh
python3 -m unittest discover -s tests -v
node --check static/app.js
```
