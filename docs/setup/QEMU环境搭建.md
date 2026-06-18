Author: Guoheng
# QEMU 环境搭建（WSL + openEuler + llama.cpp）

## 环境准备

### QEMU

```bash
# 默认安装的是8.2.2不满足赛题要求
# sudo apt install -y qemu-system
# 参考https://www.qemu.org/download/#source
sudo apt install python3-sphinx python3-sphinx-rtd-theme
sudo apt install ninja-build
sudo apt install pkg-config libglib2.0-dev
wget https://download.qemu.org/qemu-10.2.3.tar.xz
tar xvJf qemu-10.2.3.tar.xz
cd qemu-11.0.1
./configure
 make -j$(nproc)
```

### 虚拟机镜像

下载 openEuler 24.03 RISC-V 虚拟机镜像：
<https://dl-cdn.openeuler.openatom.cn/openEuler-24.03-LTS-SP3/virtual_machine_img/riscv64/>

解压：
```bash
xz -dk openEuler-24.03-LTS-SP3-riscv64.qcow2.xz
```

所需文件：
```
.
├── RISCV_VIRT_CODE_RVA20.fd     # UEFI 固件（可下载）
├── RISCV_VIRT_VARS_RVA20.fd     # UEFI 变量（可下载）
├── openEuler-24.03-LTS3-riscv64.qcow2
├── openEuler-24.03-LTS3-riscv64.qcow2.xz
└── start_vm_RVA20.sh
```

## 启动虚拟机

主机条件：CPU ≥ 8核，RAM ≥ 16G

```bash
bash start_vm_RVA20.sh
```

可选：通过 SSH 登录
```bash
ssh -p 12055 root@localhost
```

## 编译运行 llama.cpp（无 RVV）

### 安装依赖

```bash
dnf install -y git cmake
dnf install make automake gcc gcc-c++ kernel-devel
```

### 编译

```bash
git clone https://github.com/ggerganov/llama.cpp --depth 1
cd llama.cpp
mkdir build && cd build

cmake -B .. \
  -DCMAKE_BUILD_TYPE=Release \
  -DLLAMA_CURL=OFF \
  -DGGML_OPENMP=OFF \
  -DLLAMA_BUILD_EXAMPLES=ON \
  -DLLAMA_BUILD_TOOLS=ON \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_UI=OFF \
  -DGGML_RVV=OFF

cmake --build .
```

## 编译运行 llama.cpp（有 RVV）

### 修改 QEMU 启动脚本

在 `start_vm_RVA20.sh` 中，QEMU 命令行添加 CPU 参数：

```bash
-cpu rv64,v=true,vlen=256,elen=64,vext_spec=v1.0
```

### 安装 gcc-toolset-14

RVV 扩展需要 gcc-14，gcc-12 不支持。

```bash
dnf install -y git cmake
dnf install make automake gcc gcc-c++ kernel-devel
dnf install gcc-toolset-14 gcc-toolset-14-gcc gcc-toolset-14-g++ \
  gcc-toolset-14-libgcc gcc-toolset-14-libstdc++ \
  gcc-toolset-14-libstdc++-devel gcc-toolset-14-binutils
dnf install scl-utils
scl register /opt/openEuler/gcc-toolset-14/
scl enable gcc-toolset-14 bash gcc --version
```

### 编译（开启 RVV）

```bash
git clone https://github.com/ggerganov/llama.cpp --depth 1
cd llama.cpp

cmake -B build-rvv-gcc14 \
  -DCMAKE_C_COMPILER=gcc \
  -DCMAKE_CXX_COMPILER=g++ \
  -DCMAKE_BUILD_TYPE=Release \
  -DLLAMA_CURL=OFF \
  -DGGML_OPENMP=OFF \
  -DLLAMA_BUILD_EXAMPLES=ON \
  -DLLAMA_BUILD_TOOLS=ON \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_UI=OFF \
  -DGGML_CPU_RISCV64_SPACEMIT=OFF \
  -DGGML_RVV=ON \
  -DGGML_RV_ZFH=OFF \
  -DGGML_RV_ZVFH=OFF \
  -DGGML_RV_ZICBOP=OFF \
  -DGGML_RV_ZIHINTPAUSE=ON \
  -DGGML_RV_ZBA=ON \
  -DCMAKE_C_FLAGS="-march=rv64gcv_zba_zbb_zbc_zbs_zihintpause -mabi=lp64d" \
  -DCMAKE_CXX_FLAGS="-march=rv64gcv_zba_zbb_zbc_zbs_zihintpause -mabi=lp64d"

cmake --build build-rvv-gcc14
```

> 注：`llama-cli` 占用约 4.8G 内存。因为是 QEMU 模拟，速率非常低。

## 问题记录

### 1. 启动 VM 时 soft lockup

**现象**：`watchdog: BUG: soft lockup - CPU#3 stuck for 26s!`
**解决**：重新执行，或降低 vCPU、MEM 等参数。

### 2. 编译错误：unknown prefixed ISA extension `zvfh`

**原因**：编译器版本太低（gcc-12），不支持 `zvfh` 扩展。
**解决**：切换 gcc 到 14 版本（gcc-toolset-14）。

### 3. 编译错误：`__riscv_vlenb` not declared / `mulw` unrecognized

**原因**：编译器不支持 RVV 1.0 内建函数。
**解决**：使用 gcc-toolset-14，并正确设置 `-march` 参数。

## 参考

- [QEMU 启动 openEuler](https://www.openeuler.org/zh/blog/phoebe/2023-09-26-Run-openEuler-RISC-V-On-Qemu.html)
- [llama.cpp 适配 SpaceMit](https://github.com/ggml-org/llama.cpp/blob/master/docs/build-riscv64-spacemit.md)
- [llama.cpp build issue #12693](https://github.com/ggml-org/llama.cpp/issues/12693)
- [zvfh 不支持讨论](https://ruyisdk.cn/t/topic/1971)
