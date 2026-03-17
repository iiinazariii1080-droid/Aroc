# Property-Based and Fuzz Tests

Property-based тесты используют Hypothesis для генерации множества входных данных и проверки инвариантов.

## Концепция

1. **Property Tests**: Проверяем инварианты на множестве сгенерированных входов
2. **Fuzz Tests**: Генерируем случайные/некорректные данные и проверяем устойчивость

## Инварианты

### Парсеры и валидаторы
- Все валидные входы парсятся корректно
- Все невалидные входы отклоняются с правильными исключениями
- Парсеры никогда не падают (только валидные исключения)
- Exception responses ≥ 9 байт принимаются (dryve D1 может присылать 17-байтные фреймы)

### Codec
- Round-trip: `pack(value) → unpack → value`
- Все значения в диапазоне упаковываются успешно
- Выход за диапазон → ValueError

### Statusword/Controlword
- Все 16-битные значения декодируются успешно
- State inference всегда возвращает валидное состояние
- Декодированные биты соответствуют исходным

### State Machine
- Все валидные переходы успешны
- Невалидные переходы отклоняются
- Никогда не переходим в невозможные состояния
- Инварианты соблюдены во всех состояниях

## Использование

### Пример Property Test

```python
from hypothesis import given
from tests.property.hypothesis_helpers import statuswords
from drivers.dryve_d1.od.statusword import decode_statusword

@given(statuswords)
def test_decode_statusword_never_crashes(statusword):
    """Property: decode_statusword never crashes on any 16-bit value."""
    result = decode_statusword(statusword)
    assert isinstance(result, dict)
    assert "operation_enabled" in result
```

### Пример Fuzz Test

```python
from hypothesis import given
import hypothesis.strategies as st
from drivers.dryve_d1.protocol.validator import validate_mbap

@given(st.binary(min_size=0, max_size=200))
def test_fuzz_validate_mbap(random_bytes):
    """Fuzz: validate_mbap with random bytes."""
    try:
        validate_mbap(random_bytes)
    except (TelegramFormatError, TelegramValidationError):
        pass  # Expected for most random inputs
```

## Стратегии генерации

Стратегии находятся в `tests/property/hypothesis_helpers.py`:

- `statuswords` - 16-битные statusword значения
- `controlwords` - 16-битные controlword значения
- `adus` - случайные ADU (MBAP + PDU)
- `mbap_headers` - MBAP заголовки
- `indices` - OD индексы
- `signed_i32`, `unsigned_u16`, `unsigned_u32` - числовые типы

## Тесты

Все property тесты находятся в `tests/property/test_*.py`:

- `test_property_parsers.py` - property тесты парсеров/валидаторов
- `test_property_codec.py` - property тесты codec
- `test_property_statusword.py` - property тесты statusword
- `test_property_state_machine.py` - property тесты state machine

## Запуск тестов

```bash
# Запустить все property тесты
pytest tests/property/

# Запустить конкретный тест
pytest tests/property/test_property_codec.py::TestPropertyPackUnpack::test_pack_unpack_u16_roundtrip

# Запустить с verbose output
pytest tests/property/ -v

# Запустить с Hypothesis statistics
pytest tests/property/ --hypothesis-show-statistics
```

## Настройка Hypothesis

Hypothesis можно настроить через декораторы:

```python
from hypothesis import given, settings

@settings(max_examples=1000, deadline=5000)
@given(statuswords)
def test_with_custom_settings(statusword):
    ...
```

## Примечания

- Property тесты могут выполняться дольше обычных (много примеров)
- Hypothesis автоматически находит минимальные воспроизводимые примеры при ошибках
- Fuzz тесты должны быть устойчивы к любым входным данным
- Все тесты должны быть детерминированными (использовать `assume` для фильтрации)

## Зависимости

Требуется установка Hypothesis:

```bash
pip install hypothesis
```

Или добавьте в `requirements.txt`:

```
hypothesis>=6.0.0
```

