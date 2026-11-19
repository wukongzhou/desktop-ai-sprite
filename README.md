# 桌面 AI 精灵

桌面 AI 精灵包含两个配套部分：浏览器中的卡通形象创作工具，以及 Windows 桌面上的可拖拽精灵。网站负责分析上传图像并生成新角色；下载桌宠包后，桌面精灵会用性格、情绪和记忆进行动作与对话。

## 网页形象生成器

上传一张人物、宠物或插画，选择夸张萌系、怪诞超现实、几何抽象、俏皮漫画、波普贴纸或复古像素等风格，生成新的桌宠形象。图像分析和生成通过已配置的 Cloudflare Workers AI 服务进行。

## Windows 桌面精灵

生成后下载桌宠 ZIP，解压并双击 `启动精灵.bat`。桌宠支持拖拽、走动、跳跃、睡觉、情绪变化、互动和性格记忆。右键可以主动打开或关闭开机启动；首次启动不会擅自加入开机项。

桌宠的「跟它聊聊」通过 NVIDIA NIM 的 OpenAI 兼容接口工作。默认模型为 `z-ai/glm-5.3-flash`，可在精灵右键菜单的「设置 AI 接口」里填入自己的 API Key 并测试。该模型目前在 NVIDIA API Catalog 标为免费试用端点；试用额度、速率和可用模型以 NVIDIA 当前账户与服务状态为准。

## 隐私与本机数据

- 网站图像生成使用 Cloudflare；桌宠文字聊天使用 NVIDIA，两套接口互不共用密钥。
- NVIDIA Key 只保存在当前用户的本地 AppData 配置目录，不进入网站、下载包或仓库。该 JSON 配置文件为本地明文，请勿分享。
- 启动云端对话时，输入文字、精灵性格以及最多五条记忆事实会发送给 NVIDIA。避免输入密码、支付信息或其他敏感内容；NVIDIA 试用服务的数据处理以其当时的条款和隐私政策为准。
- AI 聊天只生成文字，不拥有文件操作能力。修复文件或把文件移入回收站必须由用户从桌宠菜单主动选择。

## 本机开发运行

Windows 10/11，Python 3.9+：

1. 克隆仓库并安装 Python（需包含 Tkinter）。
2. 运行 `start-desktop-pet.bat`。脚本会按需安装 Pillow，拖放扩展安装失败不会阻止启动。
3. 右键桌宠设置 NVIDIA Key；不配置 Key 时，精灵的本地动作、情绪、性格和规则回应仍可使用。

网页服务另行运行：复制 `.env.example` 为 `.env` 并按需配置 Cloudflare Workers AI；部署公网服务时把密钥放在托管平台的密钥变量中，不要提交到 Git。

## 公网测试部署

仓库包含 `Dockerfile` 与 Render Blueprint。公网版是形象生成网页，不会从云端控制访客电脑，也不会把 NVIDIA 桌宠 Key 暴露到网页。付费、个人额度和邀请奖励目前尚未实现。

## 参考

- [NVIDIA GLM-5.3-Flash API](https://build.nvidia.com/z-ai/glm-5-3-flash/build)
- [NVIDIA 模型目录](https://build.nvidia.com/models)
- [NVIDIA API 试用说明与隐私提示](https://build.nvidia.com/z-ai/glm-5-3-flash/playground)
