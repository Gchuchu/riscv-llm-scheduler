# milkV jupiter硬件使用说明

赛题要求：openEuler,openKylin,openHarmony系统\(6\.12\+内核 用于加分项：sched\_ext框架\)

Milk\-v github 下载镜像：https://github\.com/milkv\-jupiter

所有OS需要准备的硬件如下：

- 网线（如不使用wifi或热点）

- USB转TTL串口 杜邦线

- SD卡以及读卡器 用于烧录镜像

- 12V 3A或以上的电源适配器（不能用手机的电源适配器或电脑的typec口供电，功率不足）

软件如下

- balenaEtcher 用于烧录镜像

- MobaXterm 或其他串口助手用于调试

- ssh工具

# Bianbu OS

## 下载Bianbu OS

https://github\.com/milkv\-jupiter/jupiter\-bianbu\-build/

选择了minimal版本下载，无桌面但是应该用来运行llama足够

内核版本6\.6 

## 烧录系统到SD卡

下载[balenaEtcher](https://etcher.balena.io/)

https://etcher\.balena\.io/

## SD卡启动

- usb转ttl串口连接电脑和板子

- 网线接到路由器/WIFI连接

- sd卡插入

然后打开串口助手（mobaxterm）选择串口后

- 最后插电

此时能看到启动日志

## WIFI连接（无路由器）

手机或其他路由器打开热点

```Bash
# 搜索热点
sudo nmcli device wifi list
# 连接热点
sudo nmcli device wifi connect "WiFi名称" password "WiFi密码"
# sudo nmcli device wifi connect "z112g" password "66666666"
# 会输出类似内容
# Device 'wlan0' successfully activated with '9c3db9b1-e1b5-4cef-b13d-d76c9435d74a'.
# 测试
ping baidu.com
```

## 安装gpart和ssh服务

确认有网络之后\(ip a\)

```Bash
apt update
apt install gparted -y
apt update && apt install openssh-server -y
```

## 分区

在MobaXterm 里开一个新 tab SSH\(分区要使用gpart在串口无法显示，需要图形化界面，所以要使用gpart必须要ssh\)

> 我用fdisk分区把系统分坏掉了,重新刷了一个系统还是选择用gpart

```Bash
ip a
```

得到ip地址，然后Remote host 填：192\.168\.0\.101\(得到的ip\) ； Specify username，填：root； 密码：milkv （bianbu minimal）

> 如果ssh链接不上
> 
> ```Bash
> cat /etc/ssh/sshd_config | grep -E "PermitRootLogin|PasswordAuthentication"
> ```
> 
> ```Bash
> sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config
> systemctl restart ssh
> ```
> 
> 重新开一个ssh窗口即可连接

然后打开gpart

```Bash
gparted
```

> 如果看到是方块四角带有数字的乱码，就退出然后启动
> 
> ```Bash
> LANG=en_US.UTF-8 gparted
> ```

然后会出现一个GUI界面

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=NWU5Mjg4M2U0YjkwZWVhNDI5YWJmY2M3YzRmNGUzMjNfMzYwNGYzNTYzODZlOTQ5OWQ5YWY2YzllNzJlZDRlODNfSUQ6NzY1NDU1NTU2MTE0ODg3NzgwMl8xNzgyNDY2Mjc5OjE3ODI1NTI2NzlfVjM)

把0p6的空间拉满，然后点击上面的绿色确认按钮，接着点击apply，等到操作完成退出即可。

然后应该可以看到这样就分区成功了

```Bash
文件系统        大小  已用  可用 已用% 挂载点
tmpfs           784M  1.1M  783M    1% /run
/dev/mmcblk0p6   56G  1.1G   53G    2% /
tmpfs           3.9G     0  3.9G    0% /dev/shm
tmpfs           5.0M     0  5.0M    0% /run/lock
/dev/mmcblk0p5  224M   42M  165M   21% /boot
tmpfs           784M   12K  784M    1% /run/user/0
root@milkv-jupiter:~# LANG=en_US.UTF-8 gparted
```

## 交叉编译

安装编译环境和依赖：

```Bash
apt install build-essential cmake git -y
```

---

## 无RVV版

> 以下命令在宿主机上执行

### 装交叉编译工具链

在 WSL 终端里：

```Bash
sudo apt update && sudo apt install -y g++-riscv64-linux-gnu cmake
```

### 交叉编译 llama\.cpp

```Bash
cd /mnt/f/Project/OSComp/llama.cpp #下载的位置按个人情况

mkdir -p build-riscv && cd build-riscv

cmake .. \
  -DCMAKE_SYSTEM_NAME=Linux \
  -DCMAKE_SYSTEM_PROCESSOR=riscv64 \
  -DCMAKE_C_COMPILER=riscv64-linux-gnu-gcc \
  -DCMAKE_CXX_COMPILER=riscv64-linux-gnu-g++ \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=OFF \
  -DLLAMA_STATIC=ON

make -j$(nproc) llama-server
```

### 传到板子

编译完 `build-riscv/bin/llama-server`，然后 scp 传输文件：

```Bash
scp bin/llama-server root@192.168.0.101:/root/ #ip地址也按个人情况改
scp /mnt/f/Project/OSComp/models/llama-3.2-1b-instruct-q4_k_m.gguf root@192.168.0.101:/root/
```

---

### 回到板子的ssh

先给执行权限

`chmod +x /root/llama-server`

### 启动推理服务

```Bash
cd /root
./llama-server -m llama-3.2-1b-instruct-q4_k_m.gguf --port 8080 -c 512 --host 0.0.0.0
```

然后从宿主机的wsl就可以测试:

```Bash
curl -X POST http://192.168.0.101:8080/completion \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Hello, who are you?", "n_predict": 50}'
```

得到

```TypeScript
dora@J:/mnt/f/Project/OSComp/llama.cpp/build-riscv$ curl -X POST http://192.168.0.101:8080/completion \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Hello, who are you?", "n_predict": 50}'
{"index":0,"content":" Or at least, what is your purpose?\nYou see, I have been watching you, and I must say, I am intrigued. Your purpose is not as straightforward as I thought, but I am willing to learn. I have a few questions for","tokens":[],"id_slot":3,"stop":true,"model":"llama-3.2-1b-instruct-q4_k_m.gguf","tokens_predicted":50,"tokens_evaluated":7,"generation_settings":{"seed":4294967295,"temperature":0.800000011920929,"dynatemp_range":0.0,"dynatemp_exponent":1.0,"top_k":40,"top_p":0.949999988079071,"min_p":0.05000000074505806,"top_n_sigma":-1.0,"xtc_probability":0.0,"xtc_threshold":0.10000000149011612,"typical_p":1.0,"repeat_last_n":64,"repeat_penalty":1.0,"presence_penalty":0.0,"frequency_penalty":0.0,"dry_multiplier":0.0,"dry_base":1.75,"dry_allowed_length":2,"dry_penalty_last_n":512,"dry_sequence_breakers":["\n",":","\"","*"],"mirostat":0,"mirostat_tau":5.0,"mirostat_eta":0.10000000149011612,"stop":[],"max_tokens":50,"n_predict":50,"n_keep":0,"n_discard":0,"ignore_eos":false,"stream":false,"logit_bias":[],"n_probs":0,"min_keep":0,"grammar":"","grammar_lazy":false,"grammar_triggers":[],"preserved_tokens":[],"chat_format":"Content-only","reasoning_format":"deepseek","reasoning_in_content":false,"generation_prompt":"","samplers":["penalties","dry","top_n_sigma","top_k","typ_p","top_p","min_p","xtc","temperature"],"speculative.types":"none","timings_per_token":false,"post_sampling_probs":false,"backend_sampling":false,"lora":[]},"prompt":"<|begin_of_text|>Hello, who are you?","has_new_line":true,"truncated":false,"stop_type":"limit","stopping_word":"","tokens_cached":56,"timings":{"cache_n":0,"prompt_n":7,"prompt_ms":6668.685,"prompt_per_token_ms":952.6692857142858,"prompt_per_second":1.0496822087113127,"predicted_n":50,"predicted_ms":62263.19,"predicted_per_token_ms":1245.2638,"predicted_per_second":0.8030426966559213}}
```

## RVV版

> 以下命令在宿主机上执行

```Bash
sudo apt install -y g++-14-riscv64-linux-gnu


cd /mnt/f/Project/OSComp/llama.cpp  #文件夹按个人位置改
rm -rf build-riscv-rvv && mkdir build-riscv-rvv && cd build-riscv-rvv

cmake .. 
 -DCMAKE_SYSTEM_NAME=Linux 
 -DCMAKE_SYSTEM_PROCESSOR=riscv64 
 -DCMAKE_C_COMPILER=riscv64-linux-gnu-gcc-14 
 -DCMAKE_CXX_COMPILER=riscv64-linux-gnu-g++-14 
 -DCMAKE_BUILD_TYPE=Release 
 -DBUILD_SHARED_LIBS=OFF 
 -DGGML_RVV=ON 
 -DGGML_RV_ZFH=OFF 
 -DGGML_RV_ZVFH=OFF 
 -DHOST_CXX_COMPILER=g++ 
 -DLLAMA_BUILD_UI=OFF


 make -j$(nproc) llama-server


 #传输文件
 scp /mnt/f/Project/OSComp/llama.cpp/build-riscv-rvv/bin/llama-server root@192.168.0.101:/root/llama-server-rvv
```

### 回到板子ssh

```Bash
#启动llama rvv
 ./llama-server-rvv -m llama-3.2-1b-instruct-q4_k_m.gguf --port 8080 -c 512 --host 0.0.0.0
```

# OpenKylin

## 下载OS

https://www\.openkylin\.top/downloads/index\-cn\.html

选择 Spacemit K1版本

## 烧录到SD

使用[balenaEtcher](https://etcher.balena.io/)

>  在烧录前最好将iso\.zip解压缩为iso文件，直接烧录可能会出现问题

内核版本和bianbuos是一样的，6\.6

## SD卡启动

- usb转ttl串口连接电脑和板子

- 网线接到路由器/WIFI连接

- sd卡插入

然后打开串口助手（mobaxterm）

选择串口后

- 最后插电

此时能看到启动日志

username：openkylin

password：openkylin

## 分区

```Bash
sudo apt update
# 先看看分区号
sudo parted /dev/mmcblk0 print

# 扩展分区6到最大
sudo parted /dev/mmcblk0 resizepart 6 100%

#输入yes

# 扩展文件系统
sudo resize2fs /dev/mmcblk0p6
df -h
```

## 安装ssh服务

为了后续的scp服务，需要ssh，如果板子上本地编译则不需要

```Bash
sudo apt install -y openssh-server
```

## 交叉编译（RVV）

GCC 14 编译的二进制需要新版 libstdc\+\+，OpenKylin 的版本太老了,需要重新交叉编译（bianbu的版本不能直接用）

> 以下命令在wsl执行

```Bash
cd /mnt/f/Project/OSComp/llama.cpp
mkdir build-static && cd build-static

cmake .. \
  -DCMAKE_SYSTEM_NAME=Linux \
  -DCMAKE_SYSTEM_PROCESSOR=riscv64 \
  -DCMAKE_C_COMPILER=riscv64-linux-gnu-gcc-14 \
  -DCMAKE_CXX_COMPILER=riscv64-linux-gnu-g++-14 \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=OFF \
  -DGGML_RVV=ON \
  -DGGML_RV_ZFH=OFF \
  -DGGML_RV_ZVFH=OFF \
  -DLLAMA_STATIC=ON \
  -DHOST_CXX_COMPILER=g++ \
  -DLLAMA_BUILD_UI=OFF \
  -DCMAKE_EXE_LINKER_FLAGS="-static-libgcc -static-libstdc++"

make -j$(nproc) llama-server
```

通过ssh传到板子

```Bash
#ip地址通过ip a得到

scp /mnt/f/Project/OSComp/llama.cpp/build-static/bin/llama-server openkylin@192.168.0.104:/home/openkylin/llama-server-rvv
#下面这条我在pwsh执行，根据模型位置选择wsl或者pwsh
scp F:\Project\OSComp\models\llama-3.2-1b-instruct-q4_k_m.gguf openkylin@192.168.0.104:/home/openkylin/
```

### 启动推理服务

```Bash
cd /home/openkylin
chmod +x llama-server-rvv

./llama-server-rvv -m llama-3.2-1b-instruct-q4_k_m.gguf --port 8080 -c 2048 --host 0.0.0.0
```

### 日志

```Bash
openkylin@openkylin:~$ ./llama-server-rvv -m llama-3.2-1b-instruct-q4_k_m.gguf --port 8080 -c 2048 --host 0.0.0.0 --parallel 8 -lv 1
./llama-server-rvv: /lib/riscv64-linux-gnu/libstdc++.so.6: version `CXXABI_1.3.15' not found (required ./llama-server-rvv -m llama-3.2-1b-instruct-q4_k_m.gguf --port 8080 -c 2048 --host 0.0.0.0
0.00.023.024 I log_info: verbosity = 3 (adjust with the `-lv N` CLI arg)m.gguf --port 8080 -c 2048 --host 0.0.0.0
0.00.023.046 I device_info:
0.00.023.077 I   - CPU     : Spacemit(R) X60 (7832 MiB, 7832 MiB free)
0.00.023.335 I system_info: n_threads = 8 (n_threads_batch = 8) / 8 | CPU : RISCV_V = 1 | RVV_VLEN = 32 | LLAMAFILE = 1 | OPENMP = 1 | REPACK = 1 |
0.00.023.352 I srv  llama_server: n_parallel is set to auto, using n_parallel = 4 and kv_unified = true
0.00.024.544 I srv          init: using 8 threads for HTTP server
0.00.028.479 I srv         start: binding port with default address family
0.00.030.098 I srv  llama_server: loading model
0.00.030.316 I srv    load_model: loading model 'llama-3.2-1b-instruct-q4_k_m.gguf'
0.00.030.946 I common_init_result: fitting params to device memory ...
0.00.030.963 I common_init_result: (for bugs during this step try to reproduce them with -fit off, or provide --verbose logs if the bug only occurs with -fit on)
0.05.097.342 I common_params_fit_impl: projected to use 892 MiB of host memory vs. 7832 MiB of total host memory
0.09.633.874 W llama_context: n_ctx_seq (2048) < n_ctx_train (131072) -- the full capacity of the model will not be utilized
0.09.726.159 I common_init_from_params: warming up the model with an empty run - please wait ... (--no-warmup to disable)
0.09.988.810 I srv    load_model: initializing slots, n_slots = 4
0.10.252.286 W common_speculative_init: no implementations specified for speculative decoding
0.10.252.323 I slot   load_model: id  0 | task -1 | new slot, n_ctx = 2048
0.10.252.342 I slot   load_model: id  1 | task -1 | new slot, n_ctx = 2048
0.10.252.347 I slot   load_model: id  2 | task -1 | new slot, n_ctx = 2048
0.10.252.351 I slot   load_model: id  3 | task -1 | new slot, n_ctx = 2048
0.10.252.680 I srv    load_model: prompt cache is enabled, size limit: 8192 MiB
0.10.252.695 I srv    load_model: use `--cache-ram 0` to disable the prompt cache
0.10.252.700 I srv    load_model: for more info see https://github.com/ggml-org/llama.cpp/pull/16391
0.10.252.703 I srv    load_model: context checkpoints enabled, max = 32, min spacing = 256
0.10.253.370 I srv          init: idle slots will be saved to prompt cache and cleared upon starting a new task
0.10.283.454 I init: chat template, example_format: '<|start_header_id|>system<|end_header_id|>

You are a helpful assistant<|eot_id|><|start_header_id|>user<|end_header_id|>

Hello<|eot_id|><|start_header_id|>assistant<|end_header_id|>

Hi there<|eot_id|><|start_header_id|>user<|end_header_id|>

How are you?<|eot_id|><|start_header_id|>assistant<|end_header_id|>

'
0.10.298.247 I srv          init: init: chat template, thinking = 0
0.10.298.339 I srv  llama_server: model loaded
0.10.298.350 I srv  llama_server: server is listening on http://0.0.0.0:8080
0.10.298.366 I srv  update_slots: all slots are idle
```



# 无SD卡日志

```Bash
resetting ...
sys: 0x200
try sd...
bm:3
ERROR:   CMD8
ERROR:   sd f! l:76
bm:4
nor m:0xc8 d:0x4017
j...

U-Boot SPL 2022.10spacemit (Jun 06 2024 - 09:32:38 +0800)
lpddr4_silicon_init consume 11ms
Boot from fit configuration k1-x_milkv-jupiter
## Checking hash(es) for config conf_10 ... OK
## Checking hash(es) for Image uboot ... crc32+ OK
## Checking hash(es) for Image fdt_10 ... crc32+ OK
## Checking hash(es) for config config_1 ... OK
## Checking hash(es) for Image opensbi ... crc32+ OK


U-Boot 2022.10spacemit (Jun 06 2024 - 09:32:38 +0800)

CPU:   rv64imafdcv_zicsr_zifencei_zicbom_zicboz_zicbop_zihintpause_zicond_zba_zbb_zbc_zbs_svpbmt_sstc_sscofpmf
Model: Milk-V Jupiter
DRAM:  DDR size = 8192 MB
DDR size = 8192 MB
DDR size = 8192 MB
8 GiB
[RESET]probe start
[RESET]probe finish
DCDC_REG1@dcdc1: ; enabling
DCDC_REG2@dcdc2: ; enabling
DCDC_REG3@dcdc3: ; enabling
DCDC_REG4@dcdc4: ; enabling
DCDC_REG5@dcdc5: ; enabling
DCDC_REG6@dcdc6: ; enabling
LDO_REG1@ldo1: ; enabling
LDO_REG2@ldo2: ; enabling
LDO_REG3@ldo3: ; enabling
LDO_REG4@ldo4: ; enabling
LDO_REG5@ldo5: ; enabling
LDO_REG6@ldo6: ; enabling
LDO_REG7@ldo7: ; enabling
LDO_REG8@ldo8: ; enabling
LDO_REG9@ldo9: ; enabling
LDO_REG10@ldo10: ; enabling
LDO_REG11@ldo11: ; enabling
SWITCH_REG1@switch1: ; enabling
DDR size = 8192 MB
Core:  403 devices, 29 uclasses, devicetree: board
WDT:   Started PMIC_WDT with servicing (60s timeout)
WDT:   Started watchdog@D4080000 with servicing (60s timeout)
MMC:   sdh@d4280000: probe done.
sdh@d4281000: probe done.
sdh@d4280000: 0, sdh@d4281000: 2
Loading Environment from SPIFlash... k1x_qspi spi@d420c000: qspi iobase:0x0x00000000d420c000, ahb_addr:0x0x00000000b8000000, max_hz:26500000Hz
k1x_qspi spi@d420c000: rx buf size:128, tx buf size:256, ahb buf size=512
k1x_qspi spi@d420c000: AHB read enabled
k1x_qspi spi@d420c000: bus clock: 26500000Hz, PMUap reg[0xd4282860]:0x0000075b
k1x_qspi spi@d420c000: AHB buf size: 512
SF: Detected gd25q64 with page size 256 Bytes, erase size 64 KiB, total 8 MiB
*** Warning - bad CRC, using default environment

pcie_dw_k1x_probe, 651
Now init Rterm...
pcie prot id = 1, porta_init_done = 0
Now waiting portA resister tuning done...
porta redonly_reg2: 00005d37
pcie_rcal = 0x00005d37
pcie port id = 1, lane num = 2
Now int init_puphy...
waiting pll lock...
Now finish init_puphy....
pcie_dw_k1x pcie@ca400000: Unable to get phy0pcie_dw_k1x pcie@ca400000: Unable to get phy1PCIE-0: Link down
Cannot find blk device
sdh@d4281000: 74 clk wait timeout(100)
Card did not respond to voltage select! : -110
mmc_init: -95, time 19
Cannot find blk device
can not get available blk dev
initialize_console_log_buffer
Have allocated memory for console log buffer
In:    serial
Out:   serial
Err:   serial
ddr_freq_change: ddr frequency change from level 0 to 6
Change DDR data rate to 2400MT/s
Default to 100kHz
EEPROM: TlvInfo v1 len=26
TLV: 0
TlvInfo Header:
   Id String:    TlvInfo
   Version:      1
   Total Length: 26
TLV Name             Code Len Value
-------------------- ---- --- -----
Product Name         0x21  18 k1-x_milkv-jupiter
CRC-32               0xFE   4 0x4216DFE2
Checksum is valid.
Programming failed.
Generate rand serial number:
87c9c1324d57
TLV: 0
TlvInfo Header:
   Id String:    TlvInfo
   Version:      1
   Total Length: 38
TLV Name             Code Len Value
-------------------- ---- --- -----
Product Name         0x21  18 k1-x_milkv-jupiter
Base MAC Address     0x24   6 FE:FE:FE:8B:BB:C3
MAC Addresses        0x2A   2 2
CRC-32               0xFE   4 0xA50E5DD5
Checksum is valid.
Programming failed.
Cannot find TLV data: serial#
Cannot find TLV data: manufacture_date
Cannot find TLV data: manufacturer
Cannot find TLV data: device_version
Cannot find TLV data: sdk_version
spacemit_dpu_probe:video device dpu@c0340000
spacemit_dpu_probe:display device hdmi@c0400500
Found device 'hdmi@c0400500', disp_uc_priv=0000000076ed62e0
HDMI cannot get HPD signal
spacemit_display_init: device 'dpu@c0340000' display won't probe (ret=-1)
HDMI cannot get HPD signal
display devices not found or not probed yet: -1
Read PMIC reg ab value f0
sdh@d4280000: 74 clk wait timeout(100)
MMC: no card present
mmc_init: -123, time 5
Cannot find blk device
Card did not respond to voltage select! : -110
mmc_init: -95, time 17
Cannot find blk device
can not get available blk dev
Cannot find blk device
Card did not respond to voltage select! : -110
mmc_init: -95, time 18
Cannot find blk device
can not get available blk dev
Net:   RGMII interface
eth0: ethernet@cac80000
Autoboot in 0 seconds
## Error: "nand_boot" not defined
run autoboot
```

## 问题记录

### 1. OpenKylin 运行后 I/O 错误导致系统崩溃

- **问题**：OpenKylin 系统上运行 llama-server跑完 benchmark 后，出现以下两种情况：
  - **情况1**：跑 benchmark 中途提示 `bus error`，之后串口所有操作显示 `Input/Output Error`，SSH 也无法连接。
  - **情况2**：正常跑完 benchmark 后，串口显示 `Input/Output Error`，SSH 也无法连接。
    重启进入 emergency mode，有时挂机后恢复正常，有时卡住无法启动。重新烧录系统进 SD 卡后问题仍可复现。
- **解决方案**：暂无
- **分析**：已排除电源适配器和板子的问题（另一张SD卡的Bianbu OS 在相同硬件上不出此问题）。可能原因：
  - 意外断电导致 SD 卡损坏
  - OpenKylin 系统本身稳定性问题
  - 内核/驱动在压力测试后的异常状态
- **现象**：`dmesg | grep -iE "error|fail|io|sda|ata"` 日志

```Bash
root@openkylin:~# dmesg | grep -iE "error|fail|io|sda|ata" [ 0.000000] Linux version 6.6.63 (root@bianbu-24.04-build-bsp-p7lwr-xkz25) (gcc (Bianbu 13.2.0-23ubuntu4bb3) 13.2.0, GNU ld (GNU Binutils for Ubuntu) 2.42) #2.2~rc3.2 SMP PREEMPT Thu Apr 3 06:53:27 UTC 2025 [ 0.000000] SBI specification v1.0 detected [ 0.000000] SBI implementation ID=0x1 Version=0x10003 [ 0.000000] SBI IPI extension detected [ 0.000000] SBI RFENCE extension detected [ 0.000000] earlycon: sbi0 at I/O port 0x0 (options '') [ 0.000000] SBI HSM extension detected [ 0.000000] riscv: base ISA extensions acdfimv [ 0.000000] Kernel command line: earlyprintk quiet splash plymouth.ignore-serial-consoles plymouth.prefer-fbcon clk_ignore_unused swiotlb=65536 workqueue.default_affinity_scope=system rootwait rootfstype=ext4 root=UUID=ac93e93e-99e2-48b6-ada0-b8340516ef55 earlycon=sbi console=ttyS0,115200n8 loglevel=8 rdinit=/init [ 0.000000] software IO TLB: area num 8. [ 0.000000] software IO TLB: mapped [mem 0x0000000073c43000-0x000000007bc43000] (128MB) [ 0.000000] Memory: 7591516K/8388608K available (16273K kernel code, 8250K rwdata, 8192K rodata, 2307K init, 586K bss, 403876K reserved, 393216K cma-reserved) [ 0.000000] ** unsafe for production use. ** [ 0.000000] rcu: Preemptible hierarchical RCU implementation. [ 0.000000] riscv: providing IPIs using SBI IPI extension [ 0.000000] rcu: srcu_init: Setting srcu_struct sizes based on contention. [ 0.000000] riscv-timer: Timer interrupt in S-mode is available via sstc extension [ 0.000001] sched_clock: 64 bits at 24MHz, resolution 41ns, wraps every 4398046511097ns [ 0.084910] rcu: Hierarchical SRCU implementation. [ 0.156689] DMA: preallocated 1024 KiB GFP_KERNEL pool for atomic allocations [ 0.163050] DMA: preallocated 1024 KiB GFP_KERNEL|GFP_DMA32 pool for atomic allocations [ 0.221328] cpu6: Ratio of byte access time to unaligned word access is 1.63, unaligned accesses are fast [ 0.221336] cpu4: Ratio of byte access time to unaligned word access is 1.65, unaligned accesses are fast [ 0.221337] cpu5: Ratio of byte access time to unaligned word access is 2.31, unaligned accesses are fast [ 0.221337] cpu7: Ratio of byte access time to unaligned word access is 1.72, unaligned accesses are fast [ 0.221337] cpu2: Ratio of byte access time to unaligned word access is 2.76, unaligned accesses are fast [ 0.221337] cpu3: Ratio of byte access time to unaligned word access is 3.98, unaligned accesses are fast [ 0.221337] cpu1: Ratio of byte access time to unaligned word access is 2.70, unaligned accesses are fast [ 0.305398] cpu0: Ratio of byte access time to unaligned word access is 10.08, unaligned accesses are fast [ 0.312128] The real ratio of byte access time to unaligned word access should refer to the value of CPU0 [ 0.321660] Cpu0 unaligned access is more efficient than nonboot cores, because of system bandwidth preemption. [ 0.331726] Nonboot cpus' unaligned access ratio measured simultaneously, but cpu0's measure is separately [ 0.341381] suspend: SBI SUSP extension detected [ 0.480213] gpio gpiochip0: Static allocation of GPIO base is deprecated, use dynamic allocation. [ 0.502471] libata version 3.00 loaded. [ 0.633918] pps_core: Software ver. 5.3.6 - Copyright 2005-2007 Rodolfo Giometti <giometti@linux.it> [ 0.740933] Bluetooth: HCI device and connection manager initialized [ 2.044857] ntfs3: Read-only LZX/Xpress compression included [ 2.050526] fuse: init (API version 7.39) [ 2.077183] jitterentropy: Initialization failed with host not compliant with requirements: 9 [ 2.100556] xor: using function: rvv (5643 MB/sec) [ 2.118860] io scheduler mq-deadline registered [ 2.123144] io scheduler kyber registered [ 2.127156] io scheduler bfq registered [ 2.141959] k1x-dwc-pcie ca400000.pcie: has no power on gpio. [ 2.152032] k1x-dwc-pcie ca400000.pcie: IO 0x009f002000..0x009f101fff -> 0x009f002000 [ 3.288739] pci_bus 0001:00: root bus resource [io 0x0000-0xfffff] (bus address [0x9f002000-0x9f101fff]) [ 3.371530] k1x-dwc-pcie ca800000.pcie: has no power on gpio. [ 3.383957] k1x-dwc-pcie ca800000.pcie: IO 0x00b7002000..0x00b7101fff -> 0x00b7002000 [ 4.528928] pci_bus 0002:00: root bus resource [io 0x100000-0x1fffff] (bus address [0xb7002000-0xb7101fff]) [ 4.630708] d4017000.serial: ttyS0 at MMIO 0xd4017000 (irq = 74, base_baud = 921250) is a UART1 [ 4.658094] d4017100.uart: ttyS2 at MMIO 0xd4017100 (irq = 75, base_baud = 3600000) is a UART3 [ 5.092492] PPP generic driver version 2.4.2 [ 5.115276] mv-usb2-phy c0940000.usbphy: phy-k1x-ci-usb2: will select HS parallel data path [ 5.123890] mv-usb2-phy c09c0000.usbphy1: phy-k1x-ci-usb2: will select HS parallel data path [ 5.132514] mv-usb2-phy c0a30000.usb2phy: phy-k1x-ci-usb2: will select HS parallel data path [ 5.168316] mv-ehci mv-ehci1: irq 82, io mem 0xc0980100 [ 5.217598] xhci-hcd xhci-hcd.0.auto: hcc params 0x0220fe6d hci version 0x110 quirks 0x0000008000000090 [ 5.227240] xhci-hcd xhci-hcd.0.auto: irq 81, io mem 0xc0a00000 [ 5.356268] <I>CTS-SPIDrv Chipone touch driver init, version: v3.7.0-sz [ 5.613494] device-mapper: uevent: version 1.0.3 [ 5.618439] device-mapper: ioctl: 4.48.0-ioctl (2023-03-01) initialised: dm-devel@redhat.com [ 5.627469] device-mapper: multipath round-robin: version 1.2.0 loaded [ 5.634099] device-mapper: multipath queue-length: version 0.2.0 loaded [ 5.634107] device-mapper: multipath service-time: version 0.3.0 loaded [ 5.644599] device-mapper: multipath historical-service-time: version 0.1.1 loaded [ 5.699391] sdhci-spacemit d4280000.sdh: Got CD GPIO [ 5.784090] sdio: save sdio_host <- 000000005236ad6a [ 5.820625] mmcblk0: mmc0:b36b SDABC 28.9 GiB [ 5.941561] mmc2: Failed to initialize a non-removable card [ 6.016773] rproc-virtio rproc-virtio.2.auto: assigned reserved memory node vdev0buffer@30206000 [ 6.101286] virtio_rpmsg_bus virtio0: rpmsg host is online [ 6.102058] virtio_rpmsg_bus virtio0: creating channel rir-service addr 0x400 [ 6.106875] rproc-virtio rproc-virtio.2.auto: registered virtio0 (type 7) [ 6.114202] ir_spacemit virtio0.rir-service.-1.1024: new channel: 0x400 -> 0x400! [ 6.128683] virtio_rpmsg_bus virtio0: creating channel adma-service addr 0x401 [ 6.149082] adma_spacemit virtio0.adma-service.-1.1025: new channel: 0x401 -> 0x401! [ 6.152387] riscv-pmu-sbi: SBI PMU extension is available [ 6.157635] virtio_rpmsg_bus virtio0: creating channel ruart-service0 addr 0x402 [ 6.169939] pxa_k1x virtio0.ruart-service0.-1.1026: new channel: 0x402 -> 0x402! [ 6.183500] virtio_rpmsg_bus virtio0: creating channel ruart-service1 addr 0x403 [ 6.191092] pxa_k1x virtio0.ruart-service1.-1.1027: new channel: 0x403 -> 0x403! [ 6.192125] usbcore: registered new interface driver snd-usb-audio [ 6.216816] virtio_rpmsg_bus virtio0: creating channel rcpu-pwr-management-service addr 0x404 [ 6.225646] k1x_rproc virtio0.rcpu-pwr-management-service.-1.1028: new channel: 0x404 -> 0x404! [ 6.234641] virtio_rpmsg_bus virtio0: creating channel i2c-service addr 0x405 [ 6.242059] i2c_k1x virtio0.i2c-service.-1.1029: new channel: 0x405 -> 0x405! [ 6.250870] Connection create success [ 6.253047] In-situ OAM (IOAM) with IPv6 [ 6.300930] Bluetooth: BNEP (Ethernet Emulation) ver 1.3 [ 6.316567] Bluetooth: HIDP (Human Interface Emulation) ver 1.2 [ 6.342929] registered taskstats version 1 [ 6.364481] Key type fscrypt-provisioning registered [ 6.428421] suspend: SBI SUSP extension detected [ 6.543861] cfg80211: Loading compiled-in X.509 certificates for regulatory database [ 7.938650] EXT4-fs (mmcblk0p6): mounted filesystem ac93e93e-99e2-48b6-ada0-b8340516ef55 ro with ordered data mode. Quota mode: none. [ 10.322625] systemd[1]: Configuration file /run/systemd/system/netplan-ovs-cleanup.service is marked world-inaccessible. This has no effect as configuration data is accessible via APIs without restrictions. Proceeding anyway. [ 10.391022] systemd[1]: Configuration file /usr/lib/systemd/system/ostree-coredata-copy.service is marked executable. Please remove executable permission bits. Proceeding anyway. [ 10.409645] systemd[1]: Configuration file /usr/lib/systemd/system/ostree-coredata-boot.service is marked executable. Please remove executable permission bits. Proceeding anyway. [ 10.428827] systemd[1]: /etc/systemd/system/org.kylin.kaiming.service:10: Unknown key name 'StartLimitIntervalSec' in section 'Service', ignoring. [ 10.443718] systemd[1]: /etc/systemd/system/org.kaiming.systemproxy.service:10: Unknown key name 'StartLimitIntervalSec' in section 'Service', ignoring. [ 10.489308] systemd[1]: Configuration file /usr/lib/systemd/system/jpu.service is marked executable. Please remove executable permission bits. Proceeding anyway. [ 10.550307] systemd[1]: Configuration file /usr/lib/systemd/system/camera.service is marked executable. Please remove executable permission bits. Proceeding anyway. [ 10.574318] systemd[1]: Configuration file /usr/lib/systemd/system/adsp.service is marked executable. Please remove executable permission bits. Proceeding anyway. [ 10.926257] systemd[1]: Created slice user.slice - User and Session Slice. [ 11.064451] systemd[1]: Listening on systemd-fsckd.socket - fsck to fsckd communication Socket. [ 11.100620] systemd[1]: systemd-pcrextend.socket - TPM2 PCR Extension (Varlink) was skipped because of an unmet condition check (ConditionSecurity=measured-uki). [ 11.436524] systemd[1]: systemd-pcrmachine.service - TPM2 PCR Machine ID Measurement was skipped because of an unmet condition check (ConditionSecurity=measured-uki). [ 11.452350] systemd[1]: systemd-tpm2-setup-early.service - TPM2 SRK Setup (Early) was skipped because of an unmet condition check (ConditionSecurity=measured-uki). [ 11.741669] systemd[1]: Mounting sys-fs-fuse-connections.mount - FUSE Control File System... [ 11.755037] systemd[1]: Mounting sys-kernel-config.mount - Kernel Configuration File System... [ 11.779823] systemd[1]: systemd-repart.service - Repartition Root Disk was skipped because no trigger condition checks were met. [ 11.827183] systemd[1]: Mounted sys-fs-fuse-connections.mount - FUSE Control File System. [ 11.836810] systemd[1]: Mounted sys-kernel-config.mount - Kernel Configuration File System. [ 15.505804] EXT4-fs (mmcblk0p6): warning: mounting fs with errors, running e2fsck is recommended [ 16.933857] EXT4-fs (mmcblk0p5): mounted filesystem 997fb1cf-2968-40bc-9930-d1c6027fab76 r/w with ordered data mode. Quota mode: none. [ 24.752726] spacemit-wlan rf-pwrseq:wlan-pwrseq: get pwrseq ok, type: sdio [ 24.752754] spacemit-wlan rf-pwrseq:wlan-pwrseq: get pwrseq ok, type: sdio [ 24.932755] mmc1: new ultra high speed SDR104 SDIO card at address 0001 [ 24.937199] RTW: == SDIO Card Info == root@openkylin:~# tail -n 100 /var/log/syslog | grep -i "input/output error" root@openkylin:~# journalctl -k -p err 8月 09 17:50:31 openkylin kernel: db_root: cannot open: /etc/target 8月 09 17:50:31 openkylin kernel: k1x-qspi d420c000.spi: RX buffer overflow 8月 09 17:50:32 openkylin kernel: Connection create success
```

- **尝试方向**：格式化 SD 卡为空，在 Windows 上用 balenaEtcher 重新烧录。

### 2. Bianbu OS Apt 源错误

- **问题**：`apt update` 报以下错误：
  - 中科大 Ubuntu Ports 源（mantic）返回 `404 Not Found`
  - Spacemit 官方源签名无效 `EXPKEYSIG 0C1C275F85F3A22A`
- **解决方案**：暂无
- **分析**：Bianbu OS 基于 Ubuntu Mantic（23.10），Mantic 已停止维护，官方源已下架。
- **现象**：

```Bash
apt update
Get:1 http://archive.spacemit.com/bianbu-ports mantic-spacemit/snapshots/v1.0.9 InRelease [6,761 B]
Get:2 http://archive.spacemit.com/bianbu-ports mantic-porting/snapshots/v1.0.9 InRelease [6,798 B]
Ign:3 http://mirrors.ustc.edu.cn/ubuntu-ports mantic InRelease
Ign:4 http://mirrors.ustc.edu.cn/ubuntu-ports mantic-updates InRelease
Ign:5 http://mirrors.ustc.edu.cn/ubuntu-ports mantic-backports InRelease
Ign:6 http://mirrors.ustc.edu.cn/ubuntu-ports mantic-security InRelease
Err:1 http://archive.spacemit.com/bianbu-ports mantic-spacemit/snapshots/v1.0.9 InRelease
  The following signatures were invalid: EXPKEYSIG 0C1C275F85F3A22A Bianbu Repo Signing Key <bianbu@spacemit.com>
Err:7 http://mirrors.ustc.edu.cn/ubuntu-ports mantic Release
  404  Not Found [IP: 218.104.71.170 80]
Err:8 http://mirrors.ustc.edu.cn/ubuntu-ports mantic-updates Release
  404  Not Found [IP: 218.104.71.170 80]
Err:9 http://mirrors.ustc.edu.cn/ubuntu-ports mantic-backports Release
  404  Not Found [IP: 218.104.71.170 80]
Err:10 http://mirrors.ustc.edu.cn/ubuntu-ports mantic-security Release
  404  Not Found [IP: 218.104.71.170 80]
Err:2 http://archive.spacemit.com/bianbu-ports mantic-porting/snapshots/v1.0.9 InRelease
  The following signatures were invalid: EXPKEYSIG 0C1C275F85F3A22A Bianbu Repo Signing Key <bianbu@spacemit.com>
Reading package lists... Done
W: GPG error: http://archive.spacemit.com/bianbu-ports mantic-spacemit/snapshots/v1.0.9 InRelease: The following signatures were invalid: EXPKEYSIG 0C1C275F85F3A22A Bianbu Repo Signing Key <bianbu@spacemit.com>
E: The repository 'http://archive.spacemit.com/bianbu-ports mantic-spacemit/snapshots/v1.0.9 InRelease' is not signed.
N: Updating from such a repository can't be done securely, and is therefore disabled by default.
N: See apt-secure(8) manpage for repository creation and user configuration details.
E: The repository 'http://mirrors.ustc.edu.cn/ubuntu-ports mantic Release' does not have a Release file.
N: Updating from such a repository can't be done securely, and is therefore disabled by default.
N: See apt-secure(8) manpage for repository creation and user configuration details.
E: The repository 'http://mirrors.ustc.edu.cn/ubuntu-ports mantic-updates Release' does not have a Release file.
N: Updating from such a repository can't be done securely, and is therefore disabled by default.
N: See apt-secure(8) manpage for repository creation and user configuration details.
E: The repository 'http://mirrors.ustc.edu.cn/ubuntu-ports mantic-backports Release' does not have a Release file.
N: Updating from such a repository can't be done securely, and is therefore disabled by default.
N: See apt-secure(8) manpage for repository creation and user configuration details.
E: The repository 'http://mirrors.ustc.edu.cn/ubuntu-ports mantic-security Release' does not have a Release file.
N: Updating from such a repository can't be done securely, and is therefore disabled by default.
N: See apt-secure(8) manpage for repository creation and user configuration details.
W: GPG error: http://archive.spacemit.com/bianbu-ports mantic-porting/snapshots/v1.0.9 InRelease: The following signatures were invalid: EXPKEYSIG 0C1C275F85F3A22A Bianbu Repo Signing Key <bianbu@spacemit.com>
E: The repository 'http://archive.spacemit.com/bianbu-ports mantic-porting/snapshots/v1.0.9 InRelease' is not signed.
N: Updating from such a repository can't be done securely, and is therefore disabled by default.
N: See apt-secure(8) manpage for repository creation and user configuration details.
```
