from pydantic.fields import FieldInfo


def test_factory_receives_existing_values():
    field = FieldInfo(default_factory=lambda data: data["left"] + data["right"])
    assert field.get_default(call_default_factory=True, validated_data={"left": 4, "right": 5}) == 9


def test_zero_argument_factory_still_works():
    field = FieldInfo(default_factory=lambda: 7)
    assert field.get_default(call_default_factory=True, validated_data={"left": 4}) == 7
