# 工作规范

## 分支命名

所有分支从 `main` 创建，完成后通过 PR 合入 `main`。

| 前缀 | 用途 | 示例 |
|------|------|------|
| `feat/` | 新功能 | `feat/kprobe-trace-vstate` |
| `fix/` | 修复 | `fix/qemu-rvv-crash` |
| `docs/` | 文档 | `docs/benchmark-report-v1` |
| `chore/` | 杂项（配置、工具脚本等） | `chore/update-gitignore` |

## Commit 信息格式

```
<type>: <简短描述>
```

空一行后可写详细说明。类型同分支前缀：`feat` / `fix` / `docs` / `chore`。

### 示例

```
feat: 实现 kprobe 挂载 riscv_vstate_save 追踪

- kprobe 挂载内核函数 riscv_vstate_save
- 统计调用频次与每次耗时
- 支持 dirty V 被抢占事件标记
```

```
fix: 修正 QEMU RVV 编译 march 参数

移除重复的 zihintpause 扩展标记。
```

```
docs: 补充 EEVDF 调度器调研
```

## PR 规范

- PR 标题格式同 commit：`<type>: <描述>`
- PR 描述使用模板填写
- 每个 PR 关联对应 Issue（`Closes #N`）
- 合并前确保至少一名成员 Review

## 工作流程

1. 从 `main` 创建功能分支
2. 在分支上开发、提交
3. 提交 PR → 关联 Issue → 请求 Review
4. Review 通过后 squash merge 到 `main`
5. 删除已合并的分支（本地 + 远程）
