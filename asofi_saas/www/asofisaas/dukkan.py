"""دكّان — storefront.

Three lines and a template with its own copy, exactly as edupulse.py describes.
The catalogue already knew about دكّان — the card on /asofisaas appeared the
moment the SaaS Product row was saved — and pointed at a URL with no file
behind it. This is that file.
"""

from asofi_saas.asofi_saas.public.storefront import product_context

PRODUCT = "dukkan"


def get_context(context):
    return product_context(context, PRODUCT)
