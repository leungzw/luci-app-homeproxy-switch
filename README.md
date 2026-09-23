# luci-app-homeproxy-switch

给 [HomeProxy](https://github.com/immortalwrt/homeproxy) 补一个 **LuCI 总开关页面**。

> English summary: adds a "Proxy Switch" page under *Services → HomeProxy* with three buttons
> (full / self / off) that control both the running state and the boot autostart of the
> HomeProxy service. Packaged as a ready-to-install `.ipk`.

```
服务 → HomeProxy → Proxy Switch
```

页面长这样：

```
┌──────────────────────────────────────────────────────┐
│ HomeProxy 总开关                                      │
│                                                       │
│ 代理已关闭，所有流量走上级路由                         │
│ sing-box 进程：已停止　开机自启：关                    │
│                                                       │
│ [ 开启（路由器 + 局域网）] [ 仅路由器自身 ] [ 关闭代理 ]│
└──────────────────────────────────────────────────────┘
```

## 为什么需要它

luci-app-homeproxy 自带页面里**没有客户端总开关**：

- 页面上的每个 `Enable` 都只属于单个节点 / 规则 / 规则集；
- `/etc/init.d/homeproxy` 只读取服务端的 `server.enabled`，没有客户端的总开关判断；
- 于是想开关代理，只能进「系统 → 启动项」翻到 homeproxy 手动启停。

本项目补上那个开关，并且**一键同时管「当前运行状态」和「开机自启」**——不用再跑去「启动项」页面确认。

| 按钮 | 行为 |
|---|---|
| **开启（路由器 + 局域网）** | `lan_proxy_mode=except_listed`，服务 enable + 启动；局域网透明代理（国内直连） |
| **仅路由器自身** | `lan_proxy_mode=disabled`，局域网设备不走，只有路由器自己走 |
| **关闭代理** | 服务 stop + disable，并清掉可能残留的 sing-box 进程 |

典型场景：上级路由已经在跑代理时，本机再开一层就是**双层代理**，会明显变慢（实测同一个 Google 请求，单层 1.3 秒 vs 双层 5.2 秒）。有了这个开关，随时可以停掉其中一层。

## 安装

### 方式一：装预编译包（推荐）

到 [Releases](https://github.com/leungzw/luci-app-homeproxy-switch/releases) 下载对应文件：

| 文件 | 适用固件 | 包管理器 |
|---|---|---|
| `luci-app-homeproxy-switch_<ver>_all.apk` | **OpenWrt 25.x** | `apk`（ADB v3 容器）|
| `luci-app-homeproxy-switch_<ver>_all.ipk` | OpenWrt 24.10+ / ImmortalWrt 24.10+ | `opkg` |
| `luci-app-homeproxy-switch_<ver>_all.legacy.ipk` | OpenWrt 23.05 及更早 | `opkg`（传统 ar 格式）|

### OpenWrt 25.x（apk）

OpenWrt 25 换用 `apk` 作为包管理器，包是 ADB v3 容器（文件头 `ADBd`）。下载后直接装：

```sh
cd /tmp
wget https://github.com/leungzw/luci-app-homeproxy-switch/releases/latest/download/luci-app-homeproxy-switch_1.0.0-1_all.apk
apk add --allow-untrusted ./luci-app-homeproxy-switch_1.0.0-1_all.apk
```

> 24.10 之前的 opkg 固件**不认** `.apk`，请用上面的 `.ipk`；反过来 25.x 的 apk 固件也不认 `.ipk`。

### OpenWrt 24.10 / ImmortalWrt 24.10（opkg）

```sh
cd /tmp
wget https://github.com/leungzw/luci-app-homeproxy-switch/releases/latest/download/luci-app-homeproxy-switch_1.0.0-1_all.ipk
opkg install luci-app-homeproxy-switch_1.0.0-1_all.ipk
```

装完插件会自动清 LuCI 缓存并重启 rpcd；如果菜单没立刻出现，注销重新登录一次即可。

卸载：`opkg remove luci-app-homeproxy-switch`（四个文件一起删干净，无残留）。

### 方式二：手工部署

```sh
cp files/usr/bin/homeproxy-switch /usr/bin/ && chmod 755 /usr/bin/homeproxy-switch
cp files/usr/share/rpcd/acl.d/luci-app-homeproxy-switch.json  /usr/share/rpcd/acl.d/
cp files/usr/share/luci/menu.d/luci-app-homeproxy-switch.json /usr/share/luci/menu.d/
mkdir -p /www/luci-static/resources/view/homeproxy-switch
cp files/www/luci-static/resources/view/homeproxy-switch/switch.js \
   /www/luci-static/resources/view/homeproxy-switch/
/etc/init.d/rpcd restart
```

## 命令行也能用

页面背后就是一个 shell 脚本，SSH 里一样可以操作：

```sh
homeproxy-switch status    # {"running":0,"boot":0,"mode":"off"}
homeproxy-switch full      # 路由器 + 局域网
homeproxy-switch self      # 仅路由器自身
homeproxy-switch off       # 停止并关闭自启
```

`mode` 取值 `full` / `self` / `off`，`running` 和 `boot` 分别是进程状态和开机自启状态。

## 自己构建

这个包只有 shell 脚本 + JSON + 一个 LuCI JS view，**不需要 SDK 和交叉工具链**：

```sh
git clone https://github.com/leungzw/luci-app-homeproxy-switch
cd luci-app-homeproxy-switch
python3 tools/build_ipk.py          # 输出到 build/，同时生成两种容器格式
python3 tools/build_ipk.py -r 2     # 改 release 号
python3 tools/build_apk.py -t v3    # OpenWrt 25 的 apk（ADB v3 容器）
```

`build_apk.py` 不需要任何工具链，纯 Python 直接拼出 apk：
`SOURCE_DATE_EPOCH=0 python3 tools/build_apk.py -t v3` 可复现构建。
（v2 是 Alpine 旧格式，apk-tools 3 在 OpenWrt 25 上实际用的是 v3，故默认只产 v3。）

有 OpenWrt SDK 的话也可以用标准流程编译：

```sh
cp -r luci-app-homeproxy-switch ~/openwrt-sdk/package/
cd ~/openwrt-sdk && make package/luci-app-homeproxy-switch/compile V=s
```

## 关于 ipk 的两种容器格式

这是踩过坑才发现的：**OpenWrt 24.10 之后，官方源的 `.ipk` 已经不再是 ar 归档了**。

```
旧格式（≤ 23.05）  debian-binary + control.tar.gz + data.tar.gz  →  ar 归档
新格式（24.10+）   gzip( tar( ./debian-binary, ./control.tar.gz, ./data.tar.gz ) )
```

新版 opkg 读到传统 ar 包会直接报 `pkg_init_from_file: Malformed package file`。
验证很简单，从源里抓一个官方小包看文件头：

```sh
opkg download rpcd-mod-file && head -c2 rpcd-mod-file*.ipk | xxd   # 1f 8b = gzip，不是 ar
```

所以本仓库同时产出两份：默认 `.ipk` 是新格式，`.legacy.ipk` 是传统 ar 格式。

## 给 LuCI 写「会执行脚本的页面」时必看的坑

**rpcd 的 ACL 里，`file` 不是 ubus 对象，而是 `read` / `write` 下的路径白名单：**

```json
// 错误：权限完全没授予，页面只会显示"读取状态失败"
"read": { "ubus": { "file": [ "exec" ] } }

// 正确
"read":  { "file": { "/usr/bin/homeproxy-switch": [ "exec" ] } },
"write": { "file": { "/usr/bin/homeproxy-switch": [ "exec" ] } }
```

还有一个很容易误导自己的地方：**用 `ubus call file exec` 走本地 socket 是 root 全权限，会绕过 ACL**，
测出来"能用"不代表网页上能用。要复现浏览器的行为，必须走 HTTP 端点：

```sh
SID=$(curl -s -d '{"jsonrpc":"2.0","id":1,"method":"call","params":["00000000000000000000000000000000","session","login",{"username":"root","password":""}]}' http://127.0.0.1/ubus | grep -o '"ubus_rpc_session":"[a-f0-9]*"' | cut -d'"' -f4)

# result 里的第一个数字是 ubus 状态码：0 成功，6 权限不足
curl -s -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"call\",\"params\":[\"$SID\",\"file\",\"exec\",{\"command\":\"/usr/bin/homeproxy-switch\",\"params\":[\"status\"]}]}" http://127.0.0.1/ubus
```

耗时参考：`full` 约 15 秒，`off` 约 10 秒；rpcd 的超时上限在 `/etc/config/rpcd` 里默认 30 秒，够用。

## 兼容性

- `Architecture: all`（不含任何架构相关的二进制）
- 依赖：`luci-base`、`rpcd-mod-file`
- 不强依赖 luci-app-homeproxy（没装的话父菜单本身不存在，这一项也就不显示）

## 许可

MIT，见 [LICENSE](LICENSE)。
