# milkV jupiter硬件使用说明

赛题要求：openEuler,openKylin,openHarmony系统\(6\.12\+内核 用于加分项：sched\_ext框架\)

Milk\-v github 下载镜像：https://github\.com/milkv\-jupiter

所有OS需要准备的硬件如下：

- 网线（如不使用wifi或热点）

- USB转TTL串口 杜邦线

- SD卡以及读卡器 用于烧录镜像

- 12V 3A或以上的充电器

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

- usb转ttl插在电脑口

- 网线接到路由器/WIFI连接

- sd卡插入

然后打开串口助手（mobaxterm）

选择串口后

- 最后插电（typec供电不行）

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

> 如果看到一堆框四个角有数字的乱码，就退出然后启动
> 
> ```Bash
> LANG=en_US.UTF-8 gparted
> ```

然后会出现一个GUI

![Image](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/authcode/?code=NWU5Mjg4M2U0YjkwZWVhNDI5YWJmY2M3YzRmNGUzMjNfMzYwNGYzNTYzODZlOTQ5OWQ5YWY2YzllNzJlZDRlODNfSUQ6NzY1NDU1NTU2MTE0ODg3NzgwMl8xNzgyNDY2Mjc5OjE3ODI1NTI2NzlfVjM)

把0p6的空间拉满，然后点击上面的绿色钩子，apply一下等到操作完成退出即可。

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

编译完 `build-riscv/bin/llama-server`，然后 scp 传过去：

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

但是在烧录前最好将iso\.zip解压缩为iso文件，直接烧录会出现问题

内核版本和bianbuos是一样的，6\.6

## SD卡启动

- usb转ttl插在电脑口

- 网线接到路由器/WIFI连接

- sd卡插入

然后打开串口助手（mobaxterm）

选择串口后

- 最后插电（typec供电不行）

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

## 问题

Openkylin RISCV

烧Kylin，

烧一遍 

昨天跑的时候，可以正常启动，然后开启llamaserver跑完benchmark以后停掉（

情况1:有时候跑不完，开始跑就提示bus error，然后论在串口发送什么数据，都会显示Input/Output Error，此时ssh也无法连接）。

dmesg \| tail \-n 50

情况2:如果正常能跑完之后就无论在串口发送什么数据，都会显示Input/Output Error，此时ssh也无法连接。

重启会进入emergencymode，有时候挂机一会会自己进入正常模式，有时候就卡住了。

我没排查到问题来源，只可以排除是新购买的充电器问题，因为bianbuos不会出现这个问题。

情况1和情况2是在我即使重新给sd卡烧一个新系统之后仍然可以复现的问题。

（昨天跑板子的时候因为电费没了停电了一次，不知道是不是这个原因让sd卡坏了）

格式空SD卡

\[可以尝试\] SD变空，windwos去烧

## 无SD卡日志

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

# 问题记录

1. Ubuntu系统的Apt源错误

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
