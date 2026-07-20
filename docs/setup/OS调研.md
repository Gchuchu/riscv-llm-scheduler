# Milk-V Jupiter OS 调研

## 官方资源

- 官网（不曾说支持其他OS）：`https://milkv.io/zh/jupiter`
- 大批镜像参考：`https://archive.spacemit.com/`
- 内核编译参考：`https://www.spacemit.com/community/document/info?lang=zh&nodepath=software/SDK/bianbu/development/kernel_compile.md`
- 支持矩阵：`https://matrix.ruyisdk.org/`

## openEuler

- 所有镜像：`https://mirrors.yacloud.net/openeuler/`
- 参考：`http://images.oerv.ac.cn/?lang=zh_CN`
- 可能1：`https://mirrors.yacloud.net/openeuler/openEuler-preview/openEuler-24.03-SP1-LLVM-Preview/embedded_img/k1/`
- 可能2：`http://images.oerv.ac.cn/release/openeuler/25.09/milkv/jupiter/spacemit-k1-uboot-bootstd?revision=0&lang=zh_CN&doc=0`

密码参考：`https://docs.openeuler.org/zh/docs/25.09/server/quickstart/releasenotes/account_list.html`

> 注意：部分openEuler镜像没有WiFi驱动，`nmcli device wifi list` 没有设备。

## openKylin

- 下载：`https://www.openkylin.top/downloads`，选择 Spacemit K1 版本
- 内核版本 6.6.63

## openHarmony

- 下载：`https://archive.spacemit.com/image/k1/version/openharmony/`
- 内核版本 6.6

## Irradium

- 下载：`https://dl.irradium.org/irradium/images/milk_v_jupiter/`
- 版本：3.8 core 6.18
- 需要加载模块：`modprobe spacemit-card spacemit-hdmiaudio spacemit-i2s spacemit-pcm`

## Fedora

- 下载：`https://openkoji.iscas.ac.cn/pub/dist-repos/dl/Milk-V/Jupiter/images/latest/`
- 内核版本 6.1.15

## Bianbu

- Minimal 版本 K1 sdcard V2.3.5-20260601180942

---

## 尝试概况

| OS | 内核版本 | 状态 | 备注 |
|---|---|---|---|
| openEuler 6.6 | 6.6 | 可启动，有WiFi | 尝试过 |
| openKylin | 6.6.63 | 可启动 | Spacemit K1 版本 |
| openHarmony | 6.6 | 可用 | WIFI支持不好 |
| Irradium | 6.18 | 不会用 | 需手动加载模块 |
| Fedora | 6.1.15 | 无法进入 | |
| Bianbu Minimal | 6.6 | 正常使用 | 官方推荐 |

## 结论

仅仅Irraidum OS直接支持6.12+内核，但是用法较为硬核，建议更换rootfs后进行使用。更换参考OS移植方法.md
