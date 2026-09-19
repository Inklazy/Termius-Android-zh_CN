# Termius Android 中文版（Google Play 最新版自动构建）

[![Localize Android](https://github.com/Inklazy/Termius-Android-zh_CN/actions/workflows/localize-android.yml/badge.svg)](https://github.com/Inklazy/Termius-Android-zh_CN/actions/workflows/localize-android.yml)

这是一个基于 [ArcSurge/Termius-Pro-zh_CN][upstream] 的社区 Fork，重点维护 Termius Android APK 的中文构建流程。桌面端相关脚本和说明仍保留自上游；本 Fork 的主要改动集中在 `android/` 和 Android GitHub Actions。

> 当前已实际验证：可以从 Google Play 获取并构建 Termius Android `7.10.0` 中文版。

## 项目说明

- 本项目仅提供社区汉化和自动构建流程，不代表 Termius 官方。
- Termius 包名：`com.server.auditor.ssh.client`
- Android 构建产物通过 GitHub Actions Artifact 提供，不默认发布到 Releases。
- 本项目不提供破解、解锁付费功能或 Pro 权益。

## Android 构建流程

当前 [Localize Android][localize-android] workflow 的流程为：

```text
Google Play 下载
  → apkeep 下载 split APK
  → APKEditor merge
  → APKEditor decode
  → 按新版默认资源表合并中文翻译
  → APKEditor rebuild
  → zipalign
  → apksigner 签名
  → aapt / apksigner 校验
  → 上传 Artifact
```

与上游版本相比，Android 流程有以下变化：

- 不再依赖 APKMirror 旧版本作为主要下载来源。
- 使用固定版本 `apkeep 1.0.0`，从 Google Play 下载：`com.server.auditor.ssh.client`。
- 支持 Google Play split APK，并交给 APKEditor 合并为单 APK。
- `versionName` 和 `versionCode` 从实际下载并 merge 后的 APK 中读取，不硬编码版本号。
- 中文翻译以新版默认 `values/strings.xml` 为资源有效性依据：
  - 新版仍存在且已有中文资源的 key：替换；
  - 新版仍存在但官方没有中文资源的 key：新增到 `values-zh-rCN`；
  - 新版已经删除的旧 key：跳过并记录 warning；
  - 新版新增但暂无翻译的 key：保留默认英文 fallback。
- 构建会检查 Termius 自身的 `connect`、`add_host`、`all_hosts` 等资源确实被加入中文资源，避免出现“Action 成功但界面仍基本为英文”。
- 后续 Google Play 更新后，理论上无需手动修改版本号即可继续构建最新版；实际是否能下载和重打包仍取决于 Google Play、apkeep、APKEditor 及 Termius 资源结构的兼容性。

## 自行构建 Android APK

### 1. Fork 并启用 Actions

1. Fork 本仓库：[`Inklazy/Termius-Android-zh_CN`][repository]。
2. Fork 后进入仓库的 **Settings → Actions → General**。
3. 确认允许 GitHub Actions 运行，并启用 workflow。
4. 打开 **Actions → Localize Android**，确认可以看到 `Run workflow`。

### 2. 配置 GitHub Actions Secrets

进入：

```text
Settings
→ Secrets and variables
→ Actions
→ Secrets
→ New repository secret
```

配置以下 Secrets：

| Secret | 是否必须 | 用途 |
|---|---:|---|
| `GOOGLE_PLAY_EMAIL` | 是 | apkeep 登录 Google Play 使用的账号邮箱 |
| `GOOGLE_PLAY_AAS_TOKEN` | 是 | apkeep 下载 Google Play APK 的认证 Token |
| `APK_SIGN_PROPERTIES` | 是 | APK 签名配置，内容可参考 [`android/apk.sign.properties.example`][sign-example] |
| `SIGNING_KEY` | 建议 | 自己长期保存的 JKS 文件 Base64 内容 |

所有 Google Token、JKS 文件、签名密码和签名配置都禁止提交到仓库。

#### `APK_SIGN_PROPERTIES`

可以参考仓库中的示例文件，至少包含：

```properties
sign.key.alias=Termius_zh
sign.key.password=你的密钥密码
sign.key.dname.cn=你的名称
sign.key.dname.c=CN
sign.keystore=Termius_zh.jks
sign.keystore.password=你的仓库密码
```

#### `SIGNING_KEY`

建议使用自己长期保存的固定 JKS，而不是每次构建重新生成临时密钥。这样以后使用同一个 Fork 构建的新版本 APK 时，才能继续覆盖升级此前由本项目自己签名的版本。

Linux 示例：

```bash
base64 -w 0 Termius_zh.jks > Termius_zh_base64.txt
```

macOS 示例：

```bash
base64 < Termius_zh.jks | tr -d '\n' > Termius_zh_base64.txt
```

将 `Termius_zh_base64.txt` 的内容保存为 `SIGNING_KEY` Secret。Windows 可以使用等效的 Base64 编码工具生成单行内容。

### 3. 获取 `GOOGLE_PLAY_AAS_TOKEN`

`GOOGLE_PLAY_AAS_TOKEN` 只应保存到 GitHub Secrets，不要写入 workflow 或提交到 Git。

apkeep 的常用认证流程是先用一次性 Google OAuth Token 换取 AAS Token：

1. 打开 Google 的 Embedded Setup 页面并登录账号：<https://accounts.google.com/EmbeddedSetup>
2. 在浏览器开发者工具的 Network / Cookies 中找到 `oauth_token`。
3. 使用 apkeep 请求 AAS Token：

   ```bash
   apkeep -e "你的 Google Play 邮箱" --oauth-token "oauth2_4/..."
   ```

4. 将命令输出的 AAS Token 保存为 `GOOGLE_PLAY_AAS_TOKEN`。

详细认证方式以 [EFForg/apkeep][apkeep] 当前文档为准。Google 认证策略可能变化，建议使用专门的、非主力 Google 账号，并自行评估账号风险。

### 4. 手动运行构建

完成 Secrets 配置后：

1. 打开仓库的 **Actions** 页面；
2. 选择 **Localize Android**；
3. 点击 **Run workflow**；
4. 选择需要构建的分支；
5. 点击运行。

workflow 会自动下载 Google Play 最新可用版本、合并 split APK、执行汉化、重打包、签名和校验。

成功后，在该次运行页面底部的 **Artifacts** 区域下载类似下面的 Artifact：

```text
Termius_v7.10.0
```

Artifact 名称使用实际 APK 的 `versionName`，不是手动写死的版本号。

## 安装和升级注意事项

- 本项目生成的是重新签名后的修改版 APK。
- 它与 Google Play 官方 Termius 使用不同签名，通常不能直接覆盖官方版安装。
- 从官方版切换前，请先确认 Termius Vault、Hosts、Keys 等重要数据已经同步或备份。
- 后续如果一直使用同一把自己的 `SIGNING_KEY`，本项目自己构建的新版本之间可以正常覆盖升级。
- 由于不是官方签名包，Google Play 的官方更新机制不能用于更新本项目生成的 APK。

## Android 目录

```text
android/
├── apktools.py                    # Google Play 下载、split merge、汉化、重打包和签名
├── strings.xml                    # 中文翻译源
├── apk.sign.properties.example    # 签名配置示例
├── requirements.txt               # Python 依赖
└── APKEditor.jar                  # 构建时自动下载，不提交到仓库
```

本地脚本运行仍需要 Python、Java、Android build-tools（`aapt`、`zipalign`、`apksigner`）以及已配置的 Google Play 环境变量和签名配置。日常使用建议优先通过 GitHub Actions 构建。

## 桌面版说明（来自上游）

本 Fork 仍保留上游桌面端相关代码和规则，包括 `lang.py`、`rules/`、`macos/osxfix.sh` 以及桌面版 Actions。Android 改动不会自动应用到桌面版。

桌面版本地使用需要 Python；部分操作还需要 Node.js 和 `@electron/asar`：

```bash
npm install -g @electron/asar

# 默认汉化
python lang.py

# 汉化、试用规则和样式规则
python lang.py --localize --trial --style

# 仅应用试用规则
python lang.py --trial

# 还原
python lang.py --restore

# 搜索或提取字符串
python lang.py --find "term1" "term2"
python lang.py --extract

# 查看全部参数
python lang.py --help
```

如果手动替换桌面版 `app.asar`：

- Windows/Linux 可能需要关闭 Electron 的 `EnableEmbeddedAsarIntegrityValidation` 熔丝；
- macOS 替换后可运行 [`macos/osxfix.sh`](macos/osxfix.sh) 更新 hash；
- 替换前请备份原始安装目录，并注意这可能影响官方自动更新。

Windows 示例：

```bash
npx @electron/fuses write --app "C:\Users\你的用户名\AppData\Local\Programs\Termius\Termius.exe" EnableEmbeddedAsarIntegrityValidation=off
```

桌面版详细参数和发布流程以上游说明及当前仓库代码为准。

## 上游致谢与版权说明

本项目 Fork 自 [ArcSurge/Termius-Pro-zh_CN][upstream]，感谢上游项目的汉化资源、桌面端脚本和持续维护。

Termius 名称、商标和软件版权归 Termius Corporation 及相关权利方所有。本项目与 Termius Corporation 无隶属、赞助或官方合作关系。

## 免责声明

- 本项目为非官方社区汉化和构建项目，仅供学习、测试和个人使用。
- 不保证构建结果适用于所有设备、账号、Android 版本或 Termius 版本。
- Google Play 自动下载依赖非官方工具 apkeep，Google 的认证方式、账号策略和下载接口未来可能变化。
- 使用者应自行评估 Google 账号、签名密钥、数据和设备风险；推荐使用非主力 Google 账号进行自动构建。
- 请勿将 Token、JKS、密码或其他敏感信息提交到公开仓库。
- 请遵守 Termius、Google Play、apkeep 及所在地适用的服务条款和法律法规。
- 本项目不提供破解、解锁付费功能或 Pro 权益，也不应被用于任何商业出售或非法用途。

## 相关链接

- [Termius 官网][termius]
- [Termius 中文功能请求][localization]
- [本 Fork 的 GitHub Actions][actions]
- [Localize Android workflow][localize-android]
- [上游项目 ArcSurge/Termius-Pro-zh_CN][upstream]
- [EFForg/apkeep][apkeep]
- [本仓库 Android 签名配置示例][sign-example]

[repository]: https://github.com/Inklazy/Termius-Android-zh_CN
[upstream]: https://github.com/ArcSurge/Termius-Pro-zh_CN
[termius]: https://termius.com
[localization]: https://ideas.termius.com/c/82-chinese-localization
[actions]: https://github.com/Inklazy/Termius-Android-zh_CN/actions
[localize-android]: https://github.com/Inklazy/Termius-Android-zh_CN/actions/workflows/localize-android.yml
[sign-example]: https://github.com/Inklazy/Termius-Android-zh_CN/blob/main/android/apk.sign.properties.example
[apkeep]: https://github.com/EFForg/apkeep
