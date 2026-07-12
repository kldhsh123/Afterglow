# 自定义人格模板

5 个内置预设：`xuwen`（默认）/ `friend` / `lover` / `family` / `colleague`，在 `.env` 设：

```env
PERSONA_TEMPLATE=lover
```

完全自定义：

```env
PROMPT_TEMPLATE_DIR=/path/to/your/templates
PERSONA_TEMPLATE=my_template
# 会去 /path/to/your/templates/my_template.md.j2 读
```

模板可用变量：`friend_name` / `self_name` / `relationship_description` / `persona_card` / `retrieved_friend_examples` / `retrieved_dialogue_windows` / `recent_conversation` / `current_user_message` / `today` / `current_date` / `current_time` / `current_datetime` / `current_weekday` / `timezone`。其中 `retrieved_friend_examples` 会优先包含 response_pairs 样例。`persona_card` 只应作为长期语气参考，不要当作当天事实来源。

---

