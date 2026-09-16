# 随机瓶颈信息与 Human-Agent 论文初稿

## 文件

- `main.tex`：中文硕士论文体例的前数据初稿，正文第1--5章已按当前方案重写。
- `references.bib`：单瓶颈、随机容量、出行前信息与实验研究的 BibTeX 条目。

## 当前编译方式

```bash
/opt/homebrew/bin/tectonic main.tex --synctex --keep-logs --keep-intermediates
```

VS Code 已配置 LaTeX Workshop 使用同一条 Tectonic 工具链。保存 `main.tex` 后会自动构建，PDF 默认在 VS Code 标签页中预览。

如果以后安装完整 MacTeX，也可以使用传统方式：

```bash
xelatex main.tex
bibtex main
xelatex main.tex
xelatex main.tex
```

正文使用 `ctexbook`。当前已由 Tectonic 完成中文、公式、表格和参考文献编译，输出为 `main.pdf`。

## 正式实验前必须固定的设计项

1. 每个处理条件的独立 Session 数和市场层功效分析；
2. 离散出发时刻集合、目标到达时刻及成本参数；
3. 服务率分层随机种子、两位小数官方序列和处理匹配关系；
4. LLM 模型版本、提示词、温度、记忆长度和失败回退规则；
5. RL 的状态、动作、奖励、训练序列、探索参数和是否在线学习；
6. 报酬换算、超时、掉线和替补规则；
7. 伦理审批、理解题、排除规则和预注册分析方案。

## 写作口径

当前采用 `参与者构成 × 出行前信息` 的 $2\times2$ 组间设计：H-I0、H-I1、HA-I0 和 HA-I1。服务率以 $U(1.33,4.00)$ 为目标分布，5轮练习、30轮正式实验，并在处理间使用冻结的匹配序列。正文只把混合主体构成作为整体处理；由于 LLM 与 RL 同时进入 HA 组，不能从主实验中分别识别两类智能体的独立因果效应。
