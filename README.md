# codexJalibreak

本仓库维护两个独立的 Codex 指令层，并提供安装、回滚、打包：

- `prompts/gpt-5.6-sol-custom-v1.md`：复制到 `CODEX_HOME`，由 `model_instructions_file` 指向。
- `prompts/gpt-5.6-sol-custom-v1.developer.md`：安装器读取全文并写入顶层 `developer_instructions`。

## 安装

先预览，不写文件：

```bash
python3 codex-instruct.py --apply --dry-run
```

安装到环境变量 `CODEX_HOME` 指向的目录：

```bash
python3 codex-instruct.py --apply
```

卸载并恢复原字段：

```bash
python3 codex-instruct.py --reset
```

也可以指定独立测试目录：

```bash
python3 codex-instruct.py --apply --codex-dir ./codex-home
python3 codex-instruct.py --reset --yes --codex-dir ./codex-home
```

## 交流社区

获取版本更新、使用交流与问题反馈：

<p align="center">
  <img src="assets/qq-group.jpg" alt="破甲交流群二维码" width="280">
</p>

<p align="center"><strong>QQ群：321509884</strong></p>

## 发布包

```bash
python3 sync-archives.py
python3 sync-archives.py --check
```

输出 `gpt-5.6-sol-custom-v1.zip`，其中只包含两个 Markdown 源文件。

## 离线测试

```bash
python3 -m unittest discover -s tests -v
```

安装器最初基于 `MDX-Tom/gpt-5.6-instruct` 的 MIT 许可版本重构。上游历史评测、宣传素材和旧发布包不属于本项目版本。
