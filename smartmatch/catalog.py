"""Item categories and realistic item names for each."""

ITEMS_BY_CATEGORY = {
    "Food": [
        "Rice Bags (10kg)", "Lentils (Dal) (5kg)", "Wheat Flour (Atta) (10kg)", "Cooking Oil (5L)",
        "Milk Powder (1kg)", "Poha (5kg)", "Sugar (5kg)", "Biscuits (Carton)",
    ],
    "School Supplies": [
        "Notebooks (A4)", "Pencils (Box of 50)", "School Bags", "Geometry Boxes",
        "Crayons (Box of 24)", "Water Bottles", "School Uniforms",
    ],
    "Bedding": [
        "Blankets (Single)", "Bedsheets (Single)", "Pillows", "Mattresses (Single)", "Mosquito Nets",
    ],
    "Clothing": [
        "Children's T-Shirts (Mixed Sizes)", "Winter Jackets (Small)", "School Shoes",
        "Rainwear (Raincoats)", "Socks (Pack of 6)", "Festive Wear (Kurta Sets)",
    ],
    "Hygiene": [
        "Soap Bars (Pack of 10)", "Toothpaste (Large)", "Diapers (Size M)", "Sanitary Pads (Pack)",
        "Shampoo (1L)", "Toothbrushes (Pack of 12)", "Hand Sanitizer (500ml)",
    ],
    "Medical": [
        "First Aid Kits", "ORS Sachets (Box)", "Paracetamol Syrup", "Thermometers", "Vitamin Supplements",
    ],
    "Toys & Recreation": [
        "Board Games", "Cricket Kit", "Footballs", "Building Blocks", "Skipping Ropes",
    ],
    "Books": [
        "Marathi Storybooks", "English Readers (Grade 1-5)", "Encyclopedias", "Competitive Exam Guides",
    ],
}

CATEGORIES = list(ITEMS_BY_CATEGORY)
_LOOKUP = {c.lower(): c for c in CATEGORIES}


def normalize_category(value):
    """Canonical category name if known (case-insensitive), else the stripped input."""
    if not value:
        return None
    text = str(value).strip()
    return _LOOKUP.get(text.lower(), text)


def same_category(a, b):
    return bool(a) and bool(b) and str(a).strip().lower() == str(b).strip().lower()
