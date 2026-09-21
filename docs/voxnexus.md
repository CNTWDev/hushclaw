# VoxNexus 本地部署接入

Web Settings 只提供一个模型网关：VoxNexus。打开 Settings 默认进入个人中心，展示登录状态、`available` 可用额度、`reserved` 请求中冻结额度、充值套餐和主/后台模型。当前没有订阅总量，不展示剩余百分比，也不把额度单位换算成货币。

## 部署配置

安装 `pip install 'hushclaw[server]'`；已有服务可安装 `hushclaw[voxnexus]` 补充系统钥匙串依赖。macOS 使用 Keychain，Windows 使用 Credential Locker，Linux 需要已解锁的 Secret Service/libsecret/KWallet。没有可用的系统钥匙串时登录会明确报错，不降级到明文文件。

正式网关默认使用 `https://aon-ai-gateway.voxnexus.ai`，VoxAuth 默认使用 `https://auth.voxnexus.ai`，公开客户端默认使用 `hushclaw-desktop`。新部署无需再填写这三项。已有配置需要迁移或显式指定时，可在当前生效的 `hushclaw.toml` 中使用：

```toml
[provider]
name = "voxnexus"
base_url = "https://aon-ai-gateway.voxnexus.ai"
voxauth_issuer = "https://auth.voxnexus.ai"
voxauth_client_id = "hushclaw-desktop"
```

也支持 `HUSHCLAW_PROVIDER=voxnexus`、`HUSHCLAW_BASE_URL`、`HUSHCLAW_VOXAUTH_CLIENT_ID`、`HUSHCLAW_VOXAUTH_ISSUER`。网关根地址或以 `/v1` 结尾的地址均可。Settings 不提供修改部署地址、其他 router 或 API Key 的表单，也不接受浏览器提供的端点替换。旧部署仍保存其他 provider 时，Settings 自动使用内置的 VoxNexus 网关和公开客户端进行登录，不复用旧渠道的 URL 或 API Key。登录并成功保存模型后，会把 provider 切换为 VoxNexus 并清除旧模型渠道凭证；无需用户手工改配置。代码升级后需重启 Python 服务以加载新逻辑。底层旧 provider 适配器仅为已有 CLI/库调用保留。

`https://router.voxnexus.ai/v1` 是网关内部上游，HushClaw 不直接连接，也不需要上游密钥。网关要求令牌 audience 为 `voxauth`、scope 包含 `aon-gateway`；令牌由 VoxAuth 签发并由网关验证，客户端不自行拼接 JWT，也不额外假设未在接入文档中定义的 audience 授权参数。

控制台客户端 `aon-gateway-admin` 及其 `https://aon-ai-gateway.voxnexus.ai/admin/callback` 回调仅供管理控制台使用；12 小时控制台会话不等于 App access token 的有效期。App 仍按令牌返回的 `expires_in` 自动续期。

HushClaw 的客户端 Identifier 为 [`hushclaw-desktop`](https://auth.voxnexus.ai/admin/clients/hushclaw-desktop/edit)。后台需要配置为 **public/device client**，授予 `openid profile email aon-gateway`，逐条注册以下精确回调地址：

- `http://127.0.0.1:53682/callback`
- `http://127.0.0.1:53683/callback`
- `http://127.0.0.1:53684/callback`

登录使用系统默认浏览器、PKCE S256、一次性随机 state；Python 只在 `127.0.0.1` 监听第一个可用端口，五分钟超时后关闭。没有 client_secret。此流程用于浏览器和 Python 服务运行在同一台电脑的单体部署；远程服务器或手机访问另一台机器的 WebUI 需要另外部署 HTTPS 回调方案。

WebUI 主服务端口（例如 `8765`）与上述登录回调端口独立，无需把回调改成 `8765`。浏览器显示 `Authorization received` 只表示授权码已收到，登录完成仍需 Python 换取令牌并写入系统钥匙串。个人中心会分别提示令牌接口、证书、网络和钥匙串错误。VoxAuth 和网关请求使用项目公共 CA 配置，兼容默认缺少根证书的 macOS Python 安装，并保持 TLS 证书与主机名校验。

## 会话、模型与充值

access/refresh token 作为一个整体写入系统钥匙串，不进入 TOML、浏览器存储、WebSocket 或日志。所有渠道请求复用进程内同一会话，提前 60 秒串行续期；401 续期并重试一次，再失败则清除本机会话。一次部署使用一个服务进程，避免多个独立进程同时轮换同一组凭证。刷新结果不确定时也清除会话，防止重放旧 refresh token。

登录后 `GET /api/v1/me` 自动开户，不调用旧 `/auth/exchange`，不申请长期 API Key。模型列表和模型保存由前后端共同检查账号 active 且 available > 0；新用户赠送额度也算可用额度。网关对每次推理按预扣金额最终判断，余额为正仍可能返回 402。402 引导充值/减少 max_tokens，403 提示联系支持，429 指数退避。错误按 HTTP 状态码处理，错误提示附带可用的 X-Request-Id。

`/v1/models` 的模型 ID、上下文、最大输出、tags 和 pricing 用于选择器。主模型和后台模型必须来自当前网关列表，保存后使用现有运行时热重载。模型额度不足不会阻止保存未改动模型的其他系统/集成设置。

`payments_enabled=false` 隐藏套餐充值入口；后端下单前再次检查开关和套餐 ID。点击套餐创建订单并打开 Stripe Checkout。支付使用网关配置的默认完成/取消页，不另注册支付 scheme；Settings 每四秒查询订单，只有服务端返回 `paid` 才提示到账并刷新 available。自动查询最多约五分钟，之后可手动查询；关闭 Settings 暂停查询，再次打开恢复当前待付订单。页面整体刷新后订单内存状态不保留，重新查询额度仍以网关结果为准。502 显示服务/支付暂不可用，用户可向运维反馈。

本地退出立即删除钥匙串令牌并锁定模型选择；不宣称已签发的远程 access token 立即失效。

## 验证

```sh
python3 -m pytest -q tests/test_voxnexus.py tests/test_config.py tests/test_core_errors.py
node --experimental-vm-modules --test tests/test_voxnexus_ui.mjs
```

测试覆盖 PKCE/state、固定端口回退、并发轮换、401/402/403/429、禁用支付、订单查询、流式 usage/tool calling、部署地址不可被浏览器覆盖，以及登录/额度的 UI 门禁。正式网关地址和公开客户端 ID 已设为默认值。管理编辑页需要管理员登录；默认值更新不代表已核验后台类型、权限和回调配置，也不代表已完成真实账号登录或付款测试。部署状态显示 Stripe 已配置，但 UI 仍以 `/api/v1/packages` 实时返回的 `payments_enabled` 为准。
