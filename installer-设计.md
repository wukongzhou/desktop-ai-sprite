# 一键安装包设计（桌面精灵）

> 写于 2026-11-13。目标：用户从网页端直接下载安装包，双击装上，精灵就在本机
> 桌面上一直跑着。现状是网站 `POST /api/pet-pack` 打 ZIP（精灵图 + 四个 py
> 文件 + bat + 手册），要用户自己解压、装 Python、跑 bat——这只是过渡。
> 本文只讲设计，不含网站端代码。

## 0. 两级结构（一句话）

- **第 1 级：通用桌宠 .exe**——不带任何一张精灵图，人人共用同一份。由
  GitHub Actions 在 Windows 上一次打好（见 `desktop-pet.spec` 和
  `.github/workflows/build-pet-exe.yml`），产出 `DesktopPet.exe`（onedir）。
- **第 2 级：一键安装包**——用户在网站上点下载时，网站把第 1 级的通用
  .exe + 这位用户自己的精灵图 + 一个启动配置，拼成一个安装包发给用户。
  用户双击它，装完精灵就带着这张精灵图跑起来了。

这样分的好处：重的部分（打包 Python + Pillow + tkinter，约 20–30MB）
只打一次、所有人共用；每次下载只多一张精灵图（几百 KB），网站不用
每次都重新打包 Python。

## 1. 第 1 级：通用 .exe（已经搭好）

- 入口 `desktop_pet.py`，另三个文件（`pet_brain.py`、`pet_tools.py`、
  `pet_memory.py`）由它 import，PyInstaller 自动收进来。
- 第三方依赖只有两个：**Pillow（必需）**和 **tkinterdnd2（可选，拖文件
  到精灵身上才用；没装也能跑，自动降级）**。其余全是标准库（tkinter、
  winsound、ctypes 都是 Windows 版 Python 自带的）。
- 选 **onedir（文件夹）** 不选 onefile（单个 exe），三个理由写在
  `desktop-pet.spec` 开头：启动快（开机自启要立刻出现，不等解压）、
  性格档案 `pet_profile.json` 和记忆 `pet_memory.json` 能持久保存在安装
  目录（onefile 里它们会掉进临时目录，每次重掷性格、记忆全丢）、反正
  最终要包安装器，单文件的方便在这里没意义。
- 精灵图怎么传：启动参数 `--sprite 你的精灵图.png`；不传就用默认的
  `saved_sprites/latest_sprite.png`（在 exe 旁边）；找不到图会直接退出
  并打印提示。性格档案默认也在 exe 旁边的 `pet_profile.json`，记忆在
  同目录的 `pet_memory.json`，第一次运行自动掷一个。
- 打包触发：桌宠代码、spec 或工作流变了就自动打一次，产物进 Actions
  的 Artifacts（保留 90 天）；打 `v*` 标签时自动发 Release。
- 本地手动打包：在 Windows 上把四个 py 文件 + `desktop-pet.spec` +
  `build-pet-exe.bat` 放一起，双击 bat 就行（要求 python.org 的 Python
  3.12；微软商店版可能没有 tkinter）。

## 2. 第 2 级：网站把通用 .exe 拼成一键安装包

用户点下载时，网站要做的事（按顺序）：

1. 取一份第 1 级的通用 .exe（从最新的 Release / Actions 产物缓存一份
   在服务器上，别每次下载都重新打）。
2. 取这位用户刚生成的精灵图，放进安装包里的
   `saved_sprites/latest_sprite.png`（这样 .exe 不用加任何参数，默认
   就能找到这张图）。
3. 可选：顺手把这只精灵的性格档案也预掷一份放进去（`pet_profile.json`），
   这样用户第一次打开就是网站上生成时掷好的性格；不放也行，桌宠第一
   次运行会自动掷一个。
4. 用安装器把上面这套东西打成一个文件发给用户。

### 安装包两种做法的取舍（不替你选，只摆出来）

|  | NSIS 安装器 | 7-Zip SFX 自解压 |
| --- | --- | --- |
| 是什么 | 真正的安装程序（装向导），用户双击后一路“下一步” | 一个自解压 exe，解压到文件夹后自动跑一段安装命令 |
| 用户感受 | 像装正常软件：能选安装位置、有进度条、开始菜单有入口 | 解压完就装好了，没有向导，感觉更像“绿色版” |
| 卸载 | 有：控制面板“卸载程序”里能找到，自带卸载器 | 没有现成的：要自己写一段卸载逻辑（删文件夹 + 删快捷方式 + 删开机启动），或让用户手动删 |
| 开机启动 | 安装器直接写注册表 Run 键或往启动文件夹放快捷方式 | 要靠解压后跑的那段命令（bat / PowerShell）去建快捷方式 |
| 桌面图标 | 安装器建 | 同样靠解压后的命令建 |
| 服务端要装的东西 | NSIS（`makensis`）要装进网站的 Docker 镜像；还要维护一份 `.nsi` 安装脚本 | 7-Zip（`7z`）装进镜像即可；安装逻辑是一段 bat / PowerShell，比 `.nsi` 好写 |
| 打包速度 | 每次下载都要跑一遍 `makensis`（几秒） | 每次下载跑一遍 `7z a -sfx`（几秒），差不多 |
| 风险提示 | 无 | SFX 的 stub（解压头）更容易被 Windows SmartScreen / 杀毒软件拦；两种做法的 exe 目前都是未签名的，第一次运行 SmartScreen 都会弹警告，点“仍要运行”即可。想彻底不弹警告要办代码签名证书（付费），那是以后再说的事 |

共同点：两种做法都不需要管理员权限——装到 `%LOCALAPPDATA%\DesktopPet`
（用户自己的目录），不写 Program Files、不弹 UAC，用户双击就能装。
安装包体积约 20–30MB（通用 .exe 是大头，精灵图只有几百 KB）。

## 3. 用户双击安装包之后（流程）

1. 从网站下载安装包（一个 .exe，约 20–30MB）。
2. 双击 → SmartScreen 第一次会弹警告（未签名），点“仍要运行”。
3. 安装器把文件放到 `%LOCALAPPDATA%\DesktopPet`（NSIS：装向导走完；
   SFX：自解压到这个目录并跑安装命令）。
4. 建桌面图标（指向 `DesktopPet.exe`）和开始菜单入口；同时往启动
   文件夹放一个快捷方式（或写注册表 Run 键），下次开机自动启动。
5. 装完自动启动一次：精灵带着这张精灵图出现在桌面上，性格是网站
   上生成时掷好的（或第一次运行现掷的）。
6. 之后精灵一直在本地跑，不联网、不花钱；性格和记忆存在安装目录
   的 `pet_profile.json` / `pet_memory.json` 里，跟着这台电脑走。

## 4. 网站端接安装包还缺什么（清单）

- [ ] 服务器上缓存一份第 1 级的通用 .exe（从 Release / Actions 产物取），
      别每次下载都重新打。
- [ ] 下载接口：接到用户点下载时，把通用 .exe + 该用户精灵图（+
      可选的预掷性格）拼起来，现场打安装包再发下去（NSIS 就调
      `makensis`，SFX 就调 `7z a -sfx`）。
- [ ] Dockerfile 里装好打包工具（NSIS 或 7-Zip，看选哪种），并确认
      Render 的磁盘/内存够一次打包（约 20–30MB 的进出）。
- [ ] 下载按钮文案改成安装包的说法（现在还是“召唤到本机桌面”那套
      ZIP 的说法），并在页面上写清：Windows 专用、约 20–30MB、
      首次运行 SmartScreen 会弹警告。
- [ ] （以后再说）代码签名证书：办了之后 SmartScreen 不再弹警告；
      没办之前先在页面上写一句提醒。
- [ ] （以后再说）自动更新：桌宠启动时检查新版本（通用 .exe 变了才
      提示），现在没有，先手动重新下载。

## 附：本层文件清单

| 文件 | 用途 |
| --- | --- |
| `desktop-pet.spec` | PyInstaller 配置（onedir，入口 desktop_pet.py） |
| `build-pet-exe.bat` | Windows 上双击打出 .exe 的一键脚本 |
| `.github/workflows/build-pet-exe.yml` | GitHub Actions：Windows 上自动打包，上传产物；打标签发 Release |
| `installer-设计.md`（本文） | 两级结构、NSIS 与 SFX 取舍、用户流程、网站端待办 |

## 附：验证记录（2026-11-13，本 VM，Linux）

通过的：
- `desktop-pet.spec`：Python 语法解析通过。
- `.github/workflows/build-pet-exe.yml`：YAML 解析通过；文件里没有任何
  密钥、token、密码。
- 四个 py 文件在 Linux 下 `import` 全部通过；`tkinterdnd2` 没装时降级
  生效（`TkinterDnD is None`，走普通窗口），`pet_tools` / `pet_memory`
  都能正常 import。
- PyInstaller 6.22.3 在 Linux 下按 spec 跑通（`--clean --noconfirm`，
  exit 0），产出 `dist/DesktopPet/DesktopPet`（2.3MB，整个文件夹 50MB）。
  `_internal/` 里 Pillow、tkinterdnd2 都在。冻结后的 exe 能跑：
  `--help` 正常打印参数并退出 0；`--sprite` 指一个不存在的文件时，
  正确打印“Sprite file not found … Please generate a sprite in Cartoon
  Studio first.”。
- 缺失模块清单（`warn-desktop-pet.txt`）里只有 Windows 专属（winreg、
  nt、_winapi、winsound、_winreg、_wmi、msvcrt）和可选模块（numpy、
  olefile、defusedxml 等），没有一个是桌宠真正需要的。`winsound` 报
  缺失是预期的——它是 Windows 才有的标准库，Windows 上打包时自然会有。

没法在本机验的（要 Windows / 真实 Runner）：
- 真正的 Windows .exe：本 VM 是 Linux，PyInstaller 打不出 Windows 版，
  只能等 GitHub Actions 的 windows-latest 跑一次。
- `build-pet-exe.bat`：Linux 上没有 cmd.exe，没法执行；只做了人工通读，
  逻辑是：查 Python → 查 tkinter → 装依赖 → 跑 PyInstaller → 报产物路径。
- 工作流本身：没推上去跑过，YAML 语法对了不等于 Runner 上一定跑通，
  第一次跑要看 Actions 的日志。
- 安装包那一级（NSIS / SFX）：是网站端的事，本层没做。
