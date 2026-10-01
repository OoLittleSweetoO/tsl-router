# Mac Docker / Ember+ Provider

此测试部署不修改树莓派。VSM 是 Consumer，本服务是只读 Provider；不是向 VSM 主动发送 UDP。

## 启动

```sh
mkdir -p data-mac
docker compose -f compose.mac.yaml up -d --build
```

网页 `http://localhost:8080`，在“Ember+ 服务 / Provider”中开启服务并保存。
外部 TSL 发送到 Mac 的局域网 IPv4、UDP **50080**；VSM 连接同一 Mac IPv4、TCP **9000**。
请勿在 VSM 中填 localhost、树莓派 IP 或 Docker 容器内部 IP。
配置保存在 `data-mac/config.json`。容器重启后配置保留，输入灯态不跨重启保存，需重新接收一次输入。
Docker Desktop 必须运行，Mac 不能休眠。只用于受信任局域网，无认证和 TLS，不要做公网端口转发。

### 自定义 Ember+ 网口和端口

复制 `.env.example` 为 `.env`，设置后重建容器：

```ini
EMBER_BIND_IP=192.168.120.122
EMBER_PORT=9100
```

```sh
docker compose -f compose.mac.yaml up -d --force-recreate
```

`EMBER_BIND_IP` 必须是当前实际分配给 Mac 某个网卡的 IPv4；填写该地址等同于限定 Ember+ 服务网口。`0.0.0.0` 表示所有 Mac 网口。VSM 使用这个地址及 `EMBER_PORT` 连接。端口范围为 1–65535，但不能与本机其他服务冲突。

Ember+ 是 TCP Provider：VSM 主动连接服务，Provider 的回复沿同一 TCP 连接返回，因此不存在另一个可选的“发送出口”。更换 Mac 地址或端口会断开 VSM，且 Docker 宿主机端口映射无法从容器网页内热修改。

## VSM 参数

在 VSM 中配置 Ember+ Consumer/设备连接并浏览本 Provider 的目录。
具体驱动名称及逻辑绑定界面随 VSM 版本变化，实机兼容性需接入后确认。
默认 Screen=0，ID=0–10；ID 数量可配置为 1–256（范围 0 到数量减一）。
来源 `*` 代表任意源；相同 Screen/ID 由最新来源更新。多源同 ID 应配置明确的来源 IP。
Docker Desktop 可能转换源地址，以网页“输入监看”显示的实际 IP 为准。

每个 ID 节点为 `Tally/ID_n`，数字路径 `0.n`。叶参数编号固定：

| 编号 | 参数 | 类型与含义 |
| --- | --- | --- |
| 0 / 1 / 2 | Left / Right / Text | 整数：0 灭、1 红、2 绿、3 红绿同时 |
| 3 / 4 | LeftRed / LeftGreen | 左灯红、绿 Boolean |
| 5 / 6 | RightRed / RightGreen | 右灯红、绿 Boolean |
| 7 / 8 | TextRed / TextGreen | 文字灯红、绿 Boolean |
| 9 | Label | TSL 标签字符串 |
| 10 | Received | 曾收到该 ID 的有效输入 |
| 11 | Fresh | 最近输入尚未超过网页设置的过期时间 |
| 12 | SourceIP | 最后输入的来源 IPv4 |

例如 ID 1 左红为 `Tally/ID_1/LeftRed`，数字路径 `0.1.3`；左绿为 `0.1.4`。
可在 VSM 将这些布尔参数关联至所需 tally/逻辑信号。
不要把 `Fresh=false` 当作灭灯；事件驱动发送端长时间不发包属于正常情况。
输入过期后颜色保持，只有后续 TSL 改变才更新。所有参数只读；VSM 写入不会改变灯态。
颜色 3 会让对应 Red 和 Green 都为 true。

## 行为与限制

- Provider 发布原始接收灯态，与 UDP 输出映射及“暂停自动转发”独立。
- 同步周期约 100ms；用于状态监看，不是无损事件记录器，短于同步周期的脉冲可能被合并。
- 不处理 TSL 广播 Screen/ID（65535）；本版本发布配置 Screen 下的明确 ID。
- 关闭服务会断开 VSM；开启可重新浏览。修改来源、Screen 或 ID 数量会重建树并断开客户端。
- 稳定配置时数字路径不变；变更来源/Screen 会改变该路径代表的信号，应同步检查 VSM 绑定。
- Python 接收端连续不可用超过 5 秒时关闭 Provider，避免把旧数据误当成健康服务。
- Mac Docker 使用端口映射，不配置 Mac 物理网口 IP。网页网络助手仅适用于树莓派部署。
- 若修改 TSL UDP 监听端口，必须同时修改 `compose.mac.yaml` 中的 UDP 映射并重建容器。
- 需换 Ember+ 网口或端口时修改 `.env` 的 `EMBER_BIND_IP` / `EMBER_PORT` 并重建容器。

## 测试

```sh
python3 -m unittest discover -s tests -v
cd ember && npm ci --ignore-scripts && node test-provider.js
```

空白的 Mac 测试部署（没有 UDP 输出规则）还可在项目根目录运行 `node ember/test-docker.js`，使用独立 Sofie Consumer 验证 UDP→Ember+ 红/绿/灭及过期保持；会使用 ID 10 并在结束时发送灭灯、恢复原配置。

协议库：[node-emberplus](https://github.com/dufourgilles/node-emberplus)（MIT，固定版本及锁文件）；协议参考：[Lawo Ember+](https://github.com/Lawo/ember-plus)。
实际 Lawo VSM 的连接、目录浏览及逻辑绑定仍须在用户设备上验收。
