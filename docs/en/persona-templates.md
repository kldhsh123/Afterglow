# Persona templates

Five built-in templates are available: `xuwen` (default), `friend`, `lover`, `family` and `colleague`. Select one in `.env`:

```env
PERSONA_TEMPLATE=lover
```

For a custom template:

```env
PROMPT_TEMPLATE_DIR=/path/to/your/templates
PERSONA_TEMPLATE=my_template
# Reads /path/to/your/templates/my_template.md.j2
```

Available variables: `friend_name`, `self_name`, `relationship_description`, `persona_card`, `retrieved_friend_examples`, `retrieved_dialogue_windows`, `recent_conversation`, `current_user_message`, `today`, `current_date`, `current_time`, `current_datetime`, `current_weekday` and `timezone`. `retrieved_friend_examples` prioritizes response-pair examples. Treat `persona_card` as long-term style guidance, not evidence of today's facts.

Internal built-in instructions use English with output-language rules. Your custom template is preserved as written; keep its output-language policy explicit if you use multiple languages.
