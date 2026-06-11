"""Akasha 记忆引擎 — 图生长式长期记忆 Provider。

核心原则：
  The message is the only truth.
  To remember is to recommend.

架构：
  - 原始对话消息作为唯一真相源（不总结、不改写）
  - Hebbian + STDP 边生长：记忆之间通过"共同被想起"建立关联
  - 三层召回：Direct（语义相似）+ RWR（近场图扩散）+ graph_expand（远场联想）
  - noisy-OR 评分融合 + 五道反 hub 防线
  - 有界增长 + 时间衰减的自平衡图
"""
