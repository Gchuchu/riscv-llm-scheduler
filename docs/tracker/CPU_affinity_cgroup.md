Author: Chenzixuan
# CPU Affinity & cgroup

## CPU Affinity 原理

每个进程有一个 **CPU 亲和性掩码（affinity mask）**，告诉操作系统该进程允许在哪些核心上运行。

掩码（bitmap）对应 CPU 核心：
- `0b0001` → CPU 0
- `0b0011` → CPU 0,1
- `0b1111` → CPU 0,1,2,3
- `0bFF` → CPU 0~7

不给 affinity 时，内核调度器可以随时把进程迁移到任意核心。

### 软亲和

- **最后运行核心记录**：进程的 `task_struct` 中保存 `last_cpu` 字段，调度时优先尝试该核心。
- **缓存热路径优化**：若核心空闲，优先调度最近在该核心运行过的进程，利用缓存数据。

## taskset — 设置方法

```bash
# 查看进程当前绑在哪
taskset -p <PID>

# 启动时绑定到 CPU 0-3
taskset -c 0-3 ./llama-server -m model.gguf -t 4

# 运行时改绑定
taskset -pc 0-3 <PID>
```

## cgroup cpuset

cgroups 是 Linux 资源管理框架，其 **cpuset** 子系统可限制进程组的 CPU 核心和内存节点访问。

### 配置步骤

```bash
# Step 1：挂载
sudo mount -t cpuset none /sys/fs/cgroup/cpuset

# Step 2：初始化 root cpuset
echo 0-7 | sudo tee /sys/fs/cgroup/cpuset/cpuset.cpus
echo 0   | sudo tee /sys/fs/cgroup/cpuset/cpuset.mems

# Step 3：创建 group
sudo mkdir /sys/fs/cgroup/cpuset/mygroup

# Step 4：先 mems，再 cpus
echo 0   | sudo tee /sys/fs/cgroup/cpuset/mygroup/cpuset.mems
echo 0-3 | sudo tee /sys/fs/cgroup/cpuset/mygroup/cpuset.cpus

# Step 5：加入进程
echo 1234 | sudo tee /sys/fs/cgroup/cpuset/mygroup/cgroup.procs
```

### 验证是否生效

```bash
cat /proc/1234/status | grep Cpus_allowed
```

### 关于 mems

`mems` 是 NUMA 内存节点选择范围。为了防止 CPU 和内存跨 NUMA 访问导致的性能下降，需要设置 `mems`。UMA 机器不需要分配 mems，可通过 `lscpu -e` 查询。

> **注意**：WSL2 的 cpuset 功能可能有限，上述命令需要根据实际环境调整。

## 将线程池绑定到指定核心集合

### 方法一：启动时绑核（进程级）

```bash
taskset -c 0-3 ./program
# 整个进程绑定，fork 子进程继承

numactl --cpunodebind=0 --physcpubind=0-7 ./llama-server
# NUMA 机器可用，效果与 taskset 相同
```

### 方法二：pthread 绑核（线程级）

```c
cpu_set_t mask;
CPU_ZERO(&mask);
CPU_SET(0, &mask);
CPU_SET(1, &mask);
CPU_SET(2, &mask);
CPU_SET(3, &mask);

pthread_setaffinity_np(
    pthread_self(),
    sizeof(mask),
    &mask
);
```

线程级绑定，fork 的子进程不会继承。相比 taskset 控制更精细但更复杂。

### 方法三：OpenMP 环境变量

```bash
export OMP_PROC_BIND=true       # OpenMP 线程绑在核心，不迁移
export OMP_PLACES=cores         # 粒度：按物理核心绑
export OMP_NUM_THREADS=8        # OpenMP 线程数
```

## 参考

- [CPU Affinity 原理 — 知乎](https://zhuanlan.zhihu.com/p/495218391)
- [cgroup v2 — The Linux Kernel documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html)
