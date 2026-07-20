# Milk-V Jupiter OS 移植方法

---

## 新OS适配方法

**核心思路**：(Irradium 的 uboot + kernel) × openEuler 的 rootfs

**注意**：Irradium 镜像未开启 sched_ext，需要重新编译内核。

### 下载地址

- Irradium（选择 6.18.33 版本）：
  ```
  https://dl.irradium.org/irradium/images/milk_v_jupiter/irradium-3.8-riscv64-core-milk_v_jupiter-6.18.33-build-20260524.img.zst
  ```

- Rootfs（选择 openEuler 22.03 V2）：
  ```
  https://mirror.iscas.ac.cn/openeuler-sig-riscv/openEuler-RISC-V/preview/openEuler-22.03-V2-riscv64/openeuler-rootfs.tar.gz
  ```

### 取旧内核的思想

新的根文件系统中需要旧内核的固件（firmware）和内核模块（modules），将这些内容从旧根文件系统复制到新根文件系统中，更改启动分区。

### 步骤

1. 读卡器连接到虚拟机。

2. 对 SD 卡进行分区。除了已有的 `/boot`（引导分区）和 `/`（根文件系统分区）之外，还需要一个分区。可通过 `resize2fs` 缩小分区，用 `fdisk` 创建新分区，新分区作为新的根文件系统分区。

3. 挂载新 rootfs 分区和旧 rootfs 分区：
   ```bash
   tar -xpf <rootfs压缩包> -C <新分区挂载点>
   rsync -av <旧分区挂载点>/lib/modules/ <新分区挂载点>/lib/modules/
   rsync -av <旧分区挂载点>/lib/firmware/ <新分区挂载点>/lib/firmware/
   ```

4. 更改新分区的 `/etc/fstab`：
   ```
   UUID=664839f2-b9e7-43da-bf4e-58e48d733333 / ext4 defaults,noatime 0 1
   ```

5. `umount` 取消挂载，新 SD 卡放入板卡启动。

### 验证

`lsblk` 显示对应内容在对应分区中。`ls` 新分区应得到以下内容：

```
afs  bpoot  etc     lib    lost+found  mnt  proc  fun   srv  tmp  var
bin  dev    home    lib64  media       opt  root  sbin  sys  usr
```

### 问题

1. **IO 错误**
   - 解决方案：解挂错误分区后，使用 `e2fsck -f -D /dev/sdb2` 修复对应分区。

2. **启动过程中部分用户态库无法使用**
   - 解决方案：将 rootfs 压缩包重新解压至新分区，使用 `rsync -avI` 完成覆盖。

---

## 新内核适配方法

> **注意**：另一个方法（mainline 工具）[初步调研不可行，需要仓库]。
> 参考：`https://free5gc.org/blog/20250509/20250509/`
> ```bash
> sudo add-apt-repository ppa:cappelikan/ppa
> sudo apt update
> sudo apt install -y mainline
> ```

**核心思路**：在新 OS 适配的基础上，适配新的内核，开启 `SCHED_CLASS_EXT`。

### 下载地址

- Irradium 新内核固件（6.18.38）：
  ```
  https://dl.irradium.org/irradium/images/milk_v_jupiter/kernel/kernel-firmware-k1%236.18.38-1.pkg.tar.gz
  ```
- Irradium 新内核源码（6.18.38）：
  ```
  https://dl.irradium.org/irradium/images/milk_v_jupiter/kernel/kernel-source-k1%236.18.38-1.pkg.tar.gz
  ```
- config：Irradium OS 自带的 config-6.18.33 配置 + SCHED_EXT 调用所需。

### 步骤

1. 下载固件、系统源码，安装交叉编译器。

2. 复制 config，重命名后，`make menuconfig` 开启以下配置，然后编译安装：

   ```config
   CONFIG_BPF=y
   CONFIG_SCHED_CLASS_EXT=y
   CONFIG_BPF_SYSCALL=y
   CONFIG_BPF_JIT=y
   CONFIG_DEBUG_INFO_BTF=y
   CONFIG_BPF_JIT_ALWAYS_ON=y
   CONFIG_BPF_JIT_DEFAULT_ON=y
   CONFIG_PAHOLE_HAS_SPLIT_BTF=y
   # CONFIG_PAHOLE_HAS_BTF_TAG=y  （最后一个需要 clang，没开）
   ```

   编译命令：

   ```bash
   make ARCH=riscv CROSS_COMPILE=riscv64-linux-gnu- -j8 modules dtbs Image
   make ARCH=riscv CROSS_COMPILE=riscv64-linux-gnu- -j8
   ```

3. 创建目录并挂载：

   ```bash
   mount /dev/sdb1 /mnt/iboot
   mount /dev/sdb3 /mnt/irootfs
   ```

   安装至指定目录（也可先安装到临时目录查看效果）：

   ```bash
   # 临时目录安装（可选）：
   # make ARCH=riscv CROSS_COMPILE=riscv64-linux-gnu- modules_install INSTALL_MOD_PATH=/home/gh/workspace/tmprootfs
   # make ARCH=riscv CROSS_COMPILE=riscv64-linux-gnu- install INSTALL_PATH=/home/gh/workspace/tmpboot

   make ARCH=riscv CROSS_COMPILE=riscv64-linux-gnu- modules_install INSTALL_MOD_PATH=/mnt/irootfs
   make ARCH=riscv CROSS_COMPILE=riscv64-linux-gnu- install INSTALL_PATH=/mnt/iboot
   ```

   固件安装：

   ```bash
   tar -xf kernel-firmware-k1#6.18.38-1.pkg.tar.gz -C /mnt/irootfs
   ```

4. 完善 boot 分区内容（取消内存 rootfs 加载，使用分区 rootfs；如有文件缺失，从 linux 编译中复制）：

   ```bash
   ln -sfn vmlinuz-6.18.38 Image
   ln -sfn vmlinuz-6.18.38 vmlinuz
   ln -sfn System.map-6.18.38 System.map
   ln -sfn config-6.18.38 config
   rm dtb
   ln -s dtbs/6.18.38 dtb
   mv initrd.img initrd.img.disable
   ```

5. 引导启动。

### 问题

1. **每次插入 SD 卡可能挂载失败 sdb2 分区**
   - 原因尚不清楚，可能是SD卡的问题，也可能是板子上没有实时时钟，导致时间戳不同，CRC错误，不断e2fsck后出现的问题。
   - 解决方法：
     ```bash
     umount /dev/sdb2
     e2fsck -f -D /dev/sdb2
     ```
     修复后重新挂载即可。


## sched_ext验证方法

```bash
# 挂载boot的情况下查看配置
mount /dev/mmcblk0p1 /boot
cat /boot/config-$(uname -r) | grep CONFIG_SCHED_CLASS_EXT
# 有此文件
cat /sys/kernel/sched_ext/state
```