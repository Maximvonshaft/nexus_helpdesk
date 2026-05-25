# Accessibility and I18n Standard

## Accessibility

- All Email fields must have labels.
- Disabled Send button must expose reason text.
- Error messages must be linked with `aria-describedby`.
- Timeline delivery states must not rely on color alone.
- Keyboard submission must not bypass confirmation/validation.

## I18n

V1 may ship English UI copy, but all copy must be centralized and easy to translate.

Avoid hardcoded technical phrases:
- Do not show raw provider error unless sanitized.
- Do not show stack traces.
- Do not show secret refs.

## Time display

Delivery event timestamps must use the same formatting convention as existing ticket timeline.
