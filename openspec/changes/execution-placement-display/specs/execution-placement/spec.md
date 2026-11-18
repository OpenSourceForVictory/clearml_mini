## ADDED Requirements
### Requirement: 两端分配位置
运行结果 SHALL 明确显示客户端和托管服务最新执行尝试的实际节点及加速卡类型、卡号；未分配时不得使用候选位置代替。
#### Scenario: NPU 服务与 GPU 客户端
- **WHEN** 两端分别分配到 NPU 和 GPU
- **THEN** 默认任务页及服务详情分别展示正确节点和卡号
#### Scenario: 尚未分配
- **WHEN** 最新尝试尚无槽位
- **THEN** 界面提示未分配或记录不可用，不显示旧尝试位置
