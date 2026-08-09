"""نبض التعلم — storefront.

Three lines and a template with its own copy. That is what adding a product to
the storefront costs now; before this it was not possible at all.
"""

from asofi_saas.asofi_saas.public.storefront import product_context

PRODUCT = "edupulse"


def get_context(context):
    return product_context(context, PRODUCT)
