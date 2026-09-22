# luci-app-hpswitch

给 [HomeProxy](https://github.com/immortalwrt/homeproxy) 补一个 **LuCI 总开关页面**。

> English summary: adds a "Proxy Switch" page under *Services → HomeProxy* with three
> buttons (full / self / off) that control both the running state and the boot autostart
> of the HomeProxy service. Ships as a ready-to-install `.ipk`.

## 为什么需要它

luci-app-homeproxy 自带页面里**没有客户端总开关**：

- 页面上的每个 `Enable` 都只属于单个节点 / 规则 / 规则集；
- `/etc/init.d/homeproxy` 只读取服务端 `server.enabled`，没有客户端开关判断；
- 于是想开关代理，只能进「系统 → 启动项」找 homeproxy 手动启停。

这个项目补上那个开关：

| 按钮 | 行为 |
|---|---|
| **开启（路由器 + 局域网）** | `lan_proxy_mode=except_listed`，服务 enable + 启动，局域网透明代理 |
| **仅路由器自身** | `lan_proxy_mode=disabled`，局域网不走，只有路由器自己走 |
| **关闭代理** | 服务 stop + disable，并清掉可能残留的 sing-box 进程 |

三个按钮都同时管「当前运行状态」和「开机自启」，页面顶部实时显示状态。
后端脚本也可以单独在命令行用：

```sh
hpswitch status    # {"running":0,"boot":0,"mode":"off"}
hpswitch full
hpswitch self
hpswitch off
```

菜单位置：**服务 → HomeProxy → Proxy Switch**

## 安装

### 方式一：直接装预编译包（推荐）

到 Releases 页面下载对应格式的包：

| 文件 | 适用 | 安装命令 |
|---|---|---|
| `luci-app-hpswitch_<ver>_all.ipk` | OpenWrt 24.10+ / ImmortalWrt 24.10+ | `opkg install luci-app-hpswitch_1.0.0-1_all.ipk` |
| `luci-app-hpswitch_<ver>_all.legacy.ipk` | OpenWrt 23.05 及更早 | 同上，文件换成 legacy 那个 |

```sh
# 在路由器上执行
cd /tmp
wget https://github.com/CHANGE_ME/luci-app-hpswitch/releases/latest/download/luci-app-hpswitch_1.0.0-1_all.ipk
opkg install luci-app-hpswitch_1.0.0-1_all.ipk
```

装完如果菜单没立刻出现，注销重登 LuCI 或 `rm /tmp/luci-indexcache*` 刷新即可。

### 方式二：手工部署

```sh
cp files/usr/bin/hpswitch /usr/bin/hpswitch && chmod 755 /usr/bin/hpswitch
cp files/usr/share/rpcd/acl.d/luci-app-hpswitch.json /usr/share/rpcd/acl.d/
cp files/usr/share/luci/menu.d/luci-app-hpswitch.json /usr/share/luci/menu.d/
mkdir -p /www/luci-static/resources/view/hpswitch
cp files/www/luci-static/resources/view/hpswitch/switch.js /www/luci-static/resources/view/hpswitch/
/etc/init.d/rpcd restart
```

## 自己构建

这个包只有 shell 脚本 + JSON + 一个 LuCI JS view，不需要 SDK 和交叉工具链：

```sh
git clone https://github.com/CHANGE_ME/luci-app-hpswitch
cd luci-app-hpswitch
python3 tools/build_ipk.py          # 输出到 build/，同时生成两种格式
python3 tools/build_ipk.py -r 2     # 改 release 号
```

有 OpenWrt SDK 的话也可以用标准流程编译，把本仓库放到 `package/luci-app-hpswitch/`：

```sh
cp -r luci-app-hpswitch ~/openwrt-sdk/package/
cd ~/openwrt-sdk && make package/luci-app-hpswitch/compile V=s
```

## 关于 ipk 的两种容器格式

这是踩过坑才发现的：**OpenWrt 24.10 之后，官方源的 `.ipk` 已经不是 ar 归档了**。

```
旧格式（≤ 23.05）      debian-binary + control.tar.gz + data.tar.gz  →  ar 归档
新格式（24.10+）        gzip( tar( ./debian-binary, ./control.tar.gz, ./data.tar.gz ) )
```

新格式的 opkg 读到传统 ar 包会直接报 `Malformed package file`。本项目因此同时产出两份：
默认 `.ipk` 是新格式，`.legacy.ipk` 是传统 ar 格式。

## 给 LuCI 写「执行脚本的页面」时必看的坑

rpcd 的 ACL 里，`file` **不是** ubus 对象，而是 `read` / `write` 下的**路径白名单**：

```json
// 错误：这样写权限完全没授予，页面只会显示"读取状态失败"
"read": { "ubus": { "file": [ "exec" ] } }

// 正确
"read":  { "file": { "/usr/bin/hpswitch": [ "exec" ] } },
"write": { "file": { "/usr/bin/hpswitch": [ "exec" ] } }
```

还有一个很容易误导自己的地方：**用 `ubus call file exec` 走本地 socket 是 root 全权限，会绕过 ACL**，
测出来"能用"不代表网页上能用。要复现浏览器的行为必须走 HTTP 端点：

```sh
SID=$(curl -s -d '{"jsonrpc":"2.0","id":1,"method":"call","params":["00000000000000000000000000000000","session","login",{"username":"root","password":""}]}' http://127.0.0.1/ubus | grep -o '"ubus_rpc_session":"[a-f0-9]*"' | cut -d'"' -f4)

# result 第一个数字是 ubus 状态码：0 成功，6 权限不足
curl -s -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"call\",\"params\":[\"$SID\",\"file\",\"exec\",{\"command\":\"/usr/bin/hpswitch\",\"params\":[\"status\"]}]}" http://127.0.0.1/ubus
```

耗时参考：`full` 约 15 秒，`off` 约 10 秒，rpcd 的超时上限在 `/etc/config/rpcd` 里默认 30 秒，够用。

## 兼容性

- `Architecture: all`（没有任何架构相关的二进制）
- 依赖：`luci-base`、`rpcd-mod-file`
- 不强制依赖 luci-app-homeproxy（没装的话菜单项也不会显示，因为 HomeProxy 菜单不存在）

## 许可

MIT，见 [LICENSE](LICENSE)。发布前记得把 `Makefile` 里的 `PKG_MAINTAINER`、`PKG_SOURCE_URL`
和本 README 里的 `CHANGE_ME` 换成你自己的信息。
