"""Agent 形态 Solver：多轮工具循环（基于 Inspect AI react agent）。

用于带工作区的任务（如 issue_resolution）：solver 在安装时预取的仓库快照副本中
读取/搜索/编辑文件并运行命令，最终把工作区改动以 unified diff 作为回答产出，
进入既有的程序判定与 Judge 流程。
"""
