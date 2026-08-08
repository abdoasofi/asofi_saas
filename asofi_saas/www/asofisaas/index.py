"""منصّة أسوفي — the platform page.

This route used to be Rased's landing page. Rased now lives at
/asofisaas/rased with its copy unchanged, and this page introduces the
platform and hands the visitor to the product they came for.

The product list is the catalogue's, not a hardcoded one: a product added in
the Desk appears here, an inactive one does not. That is the whole reason the
catalogue exists — the previous version of this page could not describe a
second product at all, and when one appeared its plan was advertised through
Rased's vocabulary.
"""

from asofi_saas.asofi_saas.public.storefront import products

PLATFORM_NAME = "أسوفي"
PLATFORM_TAGLINE = "منصّة أنظمة الأعمال"


def get_context(context):
    context.no_cache = 1
    context.title = f"{PLATFORM_NAME} — {PLATFORM_TAGLINE}"
    context.platform_name = PLATFORM_NAME
    context.platform_tagline = PLATFORM_TAGLINE

    # Trial availability is decided per product, by the product's own record.
    # Advertising "ابدأ التجربة" against a product whose trial plan is unset
    # would walk a visitor into a signup that cannot complete.
    context.products = products()

    return context
