"""Public defaults contracts spanning model construction and the shared resolver."""

import pytest

from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo


def test_trusted_construction_sees_aliased_values_and_chained_defaults():
    class Invoice(BaseModel):
        subtotal: int = Field(alias="SUBTOTAL")
        taxed: int = Field(default_factory=lambda data: data["subtotal"] + 5)
        total: int = Field(default_factory=lambda data: data["taxed"] + data["subtotal"])

    expected = {"subtotal": 20, "taxed": 25, "total": 45}
    assert Invoice.model_validate({"SUBTOTAL": 20}).model_dump() == expected
    assert Invoice.model_construct(SUBTOTAL=20).model_dump() == expected
    assert Invoice.model_construct(SUBTOTAL=20, taxed=30).total == 50


def test_field_default_receives_the_supplied_mapping_intact():
    seen = []

    def calculate(data):
        seen.append(dict(data))
        return data["subtotal"] + data["delivery"]

    supplied = {"subtotal": 12, "delivery": 3}
    field = FieldInfo(default_factory=calculate)
    assert field.get_default(call_default_factory=True, validated_data=supplied) == 15
    assert seen == [supplied]
    assert supplied == {"subtotal": 12, "delivery": 3}


def test_zero_argument_factories_run_once_only_for_missing_fields():
    calls = []

    def make_default():
        calls.append("called")
        return 7

    class Invoice(BaseModel):
        total: int = Field(default_factory=make_default)

    assert Invoice.model_construct(total=9).total == 9
    assert calls == []
    assert Invoice.model_construct().total == 7
    assert calls == ["called"]
    Invoice.model_fields["total"].get_default(call_default_factory=False)
    assert calls == ["called"]


def test_static_mutable_defaults_are_independent_and_missing_data_still_errors():
    class Invoice(BaseModel):
        labels: list[str] = []

    first, second = Invoice.model_construct(), Invoice.model_construct()
    first.labels.append("sent")
    assert second.labels == []

    field = FieldInfo(default_factory=lambda data: data["subtotal"])
    with pytest.raises(ValueError, match="validated_data"):
        field.get_default(call_default_factory=True)
