# Resume field configurations
RESUME_FIELD_CONFIGURATIONS = [
    {
        "field_name": "address",
        "description": "Contact address",
        "field_type": "dict",
        "is_active": True,
        "is_custom": True,
        "is_required": False,
        "sample_values": None,
        "restrict_to_choices": False,
        "max_items": None,
        "level": "document",
        "schema": {
            "street": "str",
            "city": "str",
            "zip": "str",
            "country": "str"
        }
    },
    {
        "field_name": "experiences",
        "description": "Work experience",
        "field_type": "List[dict]",
        "is_active": True,
        "is_custom": True,
        "is_required": False,
        "sample_values": None,
        "restrict_to_choices": False,
        "max_items": 10,
        "level": "document",
        "schema": {
            "company": "str",
            "role": "str",
            "duration": "str",
            "location": "str"
        }
    },
    {
        "field_name": "educations",
        "description": "Educational background",
        "field_type": "List[dict]",
        "is_active": True,
        "is_custom": True,
        "is_required": False,
        "sample_values": None,
        "restrict_to_choices": False,
        "max_items": 5,
        "level": "document",
        "schema": {
            "institution": "str",
            "degree": "str",
            "year": "str",
            "courses": "List[str]"
        }
    }
]

