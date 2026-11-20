"""Minimal whole-task SDK result example; replace the demo decision with real evaluation."""
import json
import os

from runner.sdk import report_task_result


with open(os.environ['MINI_EVAL_INPUT'], encoding='utf-8') as source:
    task = json.load(source)

# Demo only: real applications should derive these values from their evaluation.
passed = bool(task.get('business', {}).get('demo_pass', False))
report_task_result(success=passed, metrics={'score': 1.0 if passed else 0.0},
                   summary='示例业务结果；请替换为真实评测逻辑')
