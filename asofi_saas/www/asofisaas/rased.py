"""راصد — storefront.

The page's structure, plans and trial form come from the shared shell; this
file only says which product is being sold. Adding a product is a template
with its copy plus three lines here, and nothing in the shell changes.
"""

from asofi_saas.asofi_saas.public.storefront import product_context

PRODUCT = "rased"


def get_context(context):
    return product_context(context, PRODUCT)
