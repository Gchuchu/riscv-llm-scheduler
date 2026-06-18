# 硬件配置

## 推荐板卡

| 项目 | 说明 |
|------|------|
| 型号 | Milk-V Jupiter（K1） |
| 主控 | SpacemiT K1（1.6GHz） |
| RAM | 8GB |
| RVV | 1.0, VLEN=256 |

## QEMU 验证环境

```
-cpu rv64,v=true,vlen=256,elen=64,vext_spec=v1.0
```

## 性能 Governor(防止引入噪声)

注：wsl无cpupower指令
测试时需固定到 `performance` 模式：
```bash
sudo cpupower frequency-set -g performance
```

测试完注意调回去
```bash
sudo cpupower frequency-set -g conservative
```
