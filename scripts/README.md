# 构建与运行脚本

## QEMU 启动

参见 `docs/setup/QEMU环境搭建.md`。

## 编译 llama.cpp

应提供类似如下脚本
```bash
# 无 RVV
./scripts/build-llama-norvv.sh

# 有 RVV（需 gcc-14）
./scripts/build-llama-rvv.sh
```

## 运行 benchmark

```bash
./scripts/run-benchmark.sh --concurrency 2,4,8
```
