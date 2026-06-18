Author: Guoheng
# WSL 安装运行 llama.cpp

## 前提

已有 WSL（Windows Subsystem for Linux）。

## 步骤

参考：[llama.cpp 官方安装指南](https://llama-cpp.com/getting-started/#how-to-install-llama-cpp-on-windows)

### 安装依赖

```bash
sudo apt update
sudo apt install -y git build-essential cmake
```

### 编译 llama.cpp

```bash
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp
mkdir build && cd build

cmake ..
cmake --build . --config Release
```

### 下载模型

模型下载链接：
- [Llama-3.2-1B-Instruct-Q4_K_M.gguf](https://huggingface.co/hugging-quants/Llama-3.2-1B-Instruct-Q4_K_M-GGUF/blob/main/llama-3.2-1b-instruct-q4_k_m.gguf)
- 1B 模型：[Solshine/Llama-3.2-1B-Q4_K_M-GGUF](https://huggingface.co/Solshine/Llama-3.2-1B-Q4_K_M-GGUF/tree/main)
- 3B 模型：[aashish1904/Llama-3.2-3B-Q4_K_M-GGUF](https://huggingface.co/aashish1904/Llama-3.2-3B-Q4_K_M-GGUF)

模型类型说明：
- **Base**：基础模型
- **Instruct**：指令微调模型
- **Chat**：对话模型
- **Distill**：蒸馏模型
- **Math**：数学专用
- **Coder**：代码专用

### 运行

目录结构：
```
.
├── llama-3.2-1b-instruct-q4_k_m.gguf
└── llama.cpp
    ├── app
    ├── benches
    ├── build
    └── .....
```

```bash
cd llama.cpp
./build/bin/llama-cli --model ../llama-3.2-1b-instruct-q4_k_m.gguf
```

## 参考

- [llama.cpp 官方下载](https://llama-cpp.com/download/)
