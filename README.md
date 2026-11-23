# Face Unlock（Windows 人脸解锁）

基于普通 RGB 摄像头的 Windows 人脸解锁程序。它不是 Windows Hello，而是自建的 **凭据提供程序（Credential Provider）**：在真实锁屏界面后台静默识别，识别成功后自动提交登录凭据完成解锁，不需要点击任何按钮。

## 特性

- 2D 面部轮廓关键点（MediaPipe Face Landmarker）+ 归一化成对距离矩阵（EDM）匹配，仅用普通摄像头，不需要红外/3D 硬件
- 多姿态模板录入，对轻微转头/距离变化更稳
- 锁屏时自动唤醒识别，成功后自动解锁；失败不影响密码/PIN/指纹登录
- 低内存：常驻父进程约 30 MB；识别工作进程按需启动，闲置 2 分钟自动退出
- 系统托盘黄色笑脸图标；右键“状态面板”可查看进程、内存、模板参数、最近识别结果
- 状态面板支持“录入人脸”“测试识别”“释放内存”
- 标准 Windows 安装包（Inno Setup），带卸载程序、开始菜单与“添加/删除程序”条目

## 结构

```
cp/        C++ 凭据提供程序（COM，锁屏集成）
src/       Python：agent（轻量父进程/托盘/管道）、worker（识别工作进程）、
           face_engine（关键点/描述子/匹配）、enroll/verify、status_ui（状态面板）
models/    MediaPipe face_landmarker.task
scripts/   注册/卸载/账户配置/首次运行/停止代理
installer/ Inno Setup 脚本与构建脚本
```

工作方式：

1. 锁屏时，Windows 加载 `cp/FaceUnlockCP.dll`，它通过命名管道 `\\.\pipe\faceunlock` 通知常驻代理；
2. 代理按需拉起识别工作进程（OpenCV + MediaPipe）并转发识别请求；
3. 识别成功后，凭据提供程序把账户凭据按 LSA 要求的“偏移打包”格式提交，完成解锁；
4. 工作进程闲置 2 分钟后退出，内存全部释放。

## 安装

1. 运行 `FaceUnlockSetup-1.0.0.exe`（需要管理员权限）
2. 安装结束时会提示配置：
   - 输入当前 Windows 账户密码（以 DPAPI 机器级加密保存在本机）
   - 跟随摄像头提示完成多姿态人脸录入
3. 按 `Win+L` 锁屏，正对摄像头即可自动解锁

> 预编译安装包内的虚拟环境依赖本机的基础 Python（默认 `D:\Parser File\Python`）。在其它机器上请按“从源码构建”重建虚拟环境。

## 使用

- 锁屏后无需操作，正对摄像头 1–2 秒即可自动解锁
- 托盘右键：状态面板 / 释放内存 / 退出
- 状态面板：查看内存、阈值、最近识别；录入人脸、测试识别
- 重新录入后，新模板会立即生效（工作进程会被要求重启以加载新模板）

## 卸载

设置 → 应用 → 已安装的应用 → Face Unlock → 卸载，或开始菜单里的“卸载 Face Unlock”。

卸载会移除凭据提供程序注册、计划任务，并删除 `data\`（账户密码、人脸模板）与 `logs\`。

## 从源码构建

依赖：

- Windows 10/11 x64
- Visual Studio Build Tools + Windows SDK（编译 C++ 凭据提供程序）
- Python 3.13（含 pip）
- `pip install opencv-python mediapipe pywin32 pystray`
- Inno Setup 6（生成安装包）

步骤：

1. 创建虚拟环境并安装依赖：
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install opencv-python mediapipe pywin32 pystray
   ```
2. 编译凭据提供程序：运行 `cp\build.bat`（生成 `cp\FaceUnlockCP.dll`）
3. 生成安装包：
   ```powershell
   powershell -ExecutionPolicy Bypass -File installer\build.ps1
   ```
4. 产物在 `installer\output\FaceUnlockSetup-1.0.0.exe`

## 安全说明（务必阅读）

- **没有活体检测**：一张照片可能通过识别。请勿用于高安全场景。
- 账户密码以 **DPAPI 机器级** 加密保存在 `data/credential.json`，本机管理员/SYSTEM 可解密。
- 识别完全本地进行，不上传任何数据。
- 仅支持“已登录后的锁屏”；冷启动登录界面不显示（用户程序尚未启动）。
- 解锁失败不会影响密码/PIN/指纹登录；随时可通过“设置 → 应用”卸载。

## 许可

本项目代码采用 MIT 许可。第三方组件见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
