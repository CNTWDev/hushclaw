// Provider-specific setup, deliberately separate from OAuth client registration.
export const EMAIL_PROVIDERS = [
  { label:'Gmail', imap_host:'imap.gmail.com', smtp_host:'smtp.gmail.com', smtp_port:587,
    credential:'Google 应用专用密码（16 位）',
    hint:'无需开发者 ID。先开启两步验证，再为 HushClaw 生成应用专用密码；不要填写网页登录密码。若账号没有此选项，可能受组织或高级保护策略限制，请使用系统 Mail 的 Google 登录。',
    url:'https://support.google.com/mail/answer/185833?hl=zh-Hans', action:'https://myaccount.google.com/apppasswords', actionLabel:'生成应用专用密码' },
  { label:'Outlook / Hotmail', imap_host:'outlook.office365.com', smtp_host:'smtp-mail.outlook.com', smtp_port:587,
    credential:'此入口不支持 Microsoft 密码登录', blocked:true,
    hint:'Microsoft 要求 OAuth。此 IMAP 表单不能用普通密码或应用密码代替授权。Mac 用户请在系统“互联网账户”或 Mail 添加 Microsoft / Exchange 账号；HushClaw 的 macos_list_emails 工具需另行启用并授权。其他平台的直接接入需要发布方提供注册后的 OAuth 应用。',
    url:'https://support.microsoft.com/zh-cn/outlook/pop-imap-and-smtp-settings-for-outlook-com' },
  { label:'iCloud', imap_host:'imap.mail.me.com', smtp_host:'smtp.mail.me.com', smtp_port:587,
    credential:'Apple App 专用密码', hint:'使用完整的 iCloud 邮箱地址。在 Apple 账户“登录与安全”生成 App 专用密码，不是 Apple 账户密码；需要两步认证。也可直接使用 Mac 系统账户。',
    url:'https://support.apple.com/102525', action:'https://account.apple.com/', actionLabel:'打开 Apple 账户' },
  { label:'Zoho', imap_host:'imap.zoho.com', smtp_host:'smtp.zoho.com', smtp_port:465,
    credential:'Zoho 客户端凭据 / 应用专用密码', hint:'先确认套餐支持并开启 IMAP。MFA / SAML 账号使用应用专用密码。这里默认个人 .com 数据中心；付费企业及其他区域请按 Zoho“服务器配置”调整地址，不能统一套用 .com。',
    url:'https://www.zoho.com/mail/help/imap-access.html' },
  { label:'QQ Mail', imap_host:'imap.qq.com', smtp_host:'smtp.qq.com', smtp_port:465,
    credential:'QQ 邮箱授权码', hint:'在 QQ 邮箱设置中开启 IMAP/SMTP 并生成授权码。用户名使用完整邮箱，密码填授权码，不是 QQ 登录密码；企业邮箱请使用管理员提供的企业服务器。',
    url:'https://service.mail.qq.com/' },
  { label:'163 Mail', imap_host:'imap.163.com', smtp_host:'smtp.163.com', smtp_port:465,
    credential:'网易客户端授权密码', hint:'在网易邮箱设置的 POP3/SMTP/IMAP 中开启服务并生成客户端授权密码。此预设仅用于个人 163 邮箱；126、yeah 和企业邮箱需要对应的服务器。',
    url:'https://help.mail.163.com/' },
  { label:'飞书邮箱', imap_host:'imap.feishu.cn', smtp_host:'smtp.feishu.cn', smtp_port:465,
    credential:'飞书第三方客户端专用密码', hint:'管理员须允许第三方邮件客户端。在飞书桌面端“设置 → 邮箱 → 第三方客户端登录”生成专用密码，不是飞书登录密码。',
    url:'https://www.feishu.cn/hc/en-US/articles/902478147400-log-in-to-feishu-mail-through-a-third-party-email-client' },
  { label:'Custom', imap_host:'', smtp_host:'', smtp_port:587, credential:'服务商提供的客户端凭据',
    hint:'使用服务商公布的 TLS 地址和客户端凭据。IMAP 使用 TLS；SMTP 465 自动使用隐式 TLS，其他端口必须支持 STARTTLS。此表单不支持 OAuth-only 服务。' },
].map(p => ({imap_port:993, ...p}));

export function emailGuide(host) {
  const normalized = String(host || '').trim().toLowerCase().replace(/\.$/, '');
  return EMAIL_PROVIDERS.find(p => p.imap_host === normalized || p.smtp_host === normalized)
    || (/^(imap|smtp)(pro)?\.zoho\./.test(normalized) ? EMAIL_PROVIDERS.find(p => p.label === 'Zoho') : EMAIL_PROVIDERS.at(-1));
}

export function calendarGuide(url) {
  let host;
  try { host = new URL(String(url).includes('://') ? url : 'https://' + url).hostname; } catch { host = ''; }
  if (['google.com','www.google.com','calendar.google.com','apidata.googleusercontent.com'].includes(host))
    return 'Google Calendar 不接受邮箱应用密码。Mac：系统互联网账户登录 Google 并开启日历，再点“管理本机日历来源”。应用内直接 OAuth 需发布方提供已注册应用。';
  if (host === 'caldav.icloud.com') return 'iCloud CalDAV：Apple 账户 + App 专用密码。Mac 用户优先复用系统日历，无需在这里再次填写密码。';
  if (host === 'caldav.fastmail.com') return 'Fastmail：使用授予日历权限的 App password；套餐须支持第三方 CalDAV，不是网页登录密码。';
  if (host === 'caldav.feishu.cn') return '请从飞书日历的第三方同步设置取得 CalDAV 地址及专用凭据，不要使用飞书邮箱密码或登录密码。组织可能限制该功能。';
  return '使用服务商提供的 HTTPS CalDAV 地址和客户端专用凭据。Nextcloud 请从日历设置复制个人 CalDAV 地址，并生成应用密码；邮箱授权不等于日历授权。';
}

// One form reader for account switching, saving and testing. A draft secret may
// follow only its original endpoint/user, never a changed provider or account.
export function syncIntegrationAccount(account, kind, doc = document) {
  if (!account) return;
  const identityKeys = kind === 'email' ? ['imap_host', 'smtp_host', 'username'] : ['url', 'username'];
  const identity = value => JSON.stringify(identityKeys.map(k => String(value[k] || '').trim()));
  const previous = identity(account);
  const fields = kind === 'email'
    ? ['label', 'username', 'imap_host', 'smtp_host', 'mailbox']
    : ['label', 'username', 'url', 'calendar_name'];
  for (const key of fields) {
    const id = key === 'calendar_name' ? 'calendar-name' : `${kind}-${key.replaceAll('_', '-')}`;
    account[key] = (doc.getElementById(id)?.value || '').trim();
  }
  account.enabled = Boolean(doc.getElementById(`${kind}-enabled`)?.checked);
  if (kind === 'email') {
    for (const key of ['imap_port', 'smtp_port']) account[key] = Number(doc.getElementById(`email-${key.replace('_', '-')}`)?.value) || account[key];
    account.mailbox ||= 'INBOX';
  }
  if (identity(account) !== previous) { account.password = ''; account.password_set = false; }
  const password = doc.getElementById(`${kind}-password`)?.value || '';
  if (password) account.password = password;
}
