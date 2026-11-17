from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts import views as acc
from apps.api.views import ApiClientViewSet
from apps.catalog import views as cat
from apps.integrations import views as integ
from apps.kitchen.views import KitchenStationViewSet, KitchenTicketViewSet
from apps.orders import views as orders
from apps.organization import views as org
from apps.payments.views import CashSessionViewSet
from apps.reporting.views import AuditLogView, SalesSummaryView

router = DefaultRouter(trailing_slash=True)
router.register("users", acc.UserViewSet, basename="user")
router.register("roles", acc.RoleViewSet, basename="role")
router.register("devices", acc.DeviceViewSet, basename="device")
router.register("franchisees", org.FranchiseeViewSet, basename="franchisee")
router.register("stores", org.StoreViewSet, basename="store")
router.register("categories", cat.CategoryViewSet, basename="category")
router.register("crusts", cat.CrustViewSet, basename="crust")
router.register("prep-categories", cat.PrepCategoryViewSet, basename="prepcategory")
router.register("tax-categories", cat.TaxCategoryViewSet, basename="taxcategory")
router.register("tax-rates", cat.TaxRateViewSet, basename="taxrate")
router.register("modifier-groups", cat.ModifierGroupViewSet, basename="modifiergroup")
router.register("modifiers", cat.ModifierViewSet, basename="modifier")
router.register("charges", cat.ChargeViewSet, basename="charge")
router.register("discounts", cat.DiscountViewSet, basename="discount")
router.register("products", cat.ProductViewSet, basename="product")
router.register("product-requests", cat.ProductRequestViewSet, basename="productrequest")
router.register("orders", orders.OrderViewSet, basename="order")
router.register("cash-sessions", CashSessionViewSet, basename="cashsession")
router.register("kitchen/stations", KitchenStationViewSet, basename="kitchenstation")
router.register("kitchen/tickets", KitchenTicketViewSet, basename="kitchenticket")
router.register("integration-providers", integ.IntegrationProviderViewSet, basename="integrationprovider")
router.register("provider-accounts", integ.ProviderAccountViewSet, basename="provideraccount")
router.register("webhook-events", integ.WebhookEventViewSet, basename="webhookevent")
router.register("integration-mappings", integ.ProductMappingViewSet, basename="productmapping")
router.register("api-clients", ApiClientViewSet, basename="apiclient")


class StoreOrdersView(orders.OrderViewSet):
    def get_queryset(self):
        return super().get_queryset().filter(store_id=self.kwargs["store_id"])


api_v1 = [
    path("auth/login/", acc.LoginView.as_view(), name="login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("auth/pos/login/", acc.POSLoginView.as_view(), name="pos-login"),
    path("auth/me/", acc.MeView.as_view(), name="me"),
    path("auth/manager-authorize/", acc.ManagerAuthorizeView.as_view(), name="manager-authorize"),
    path("headquarters/", org.HeadquartersView.as_view(), name="headquarters"),
    path("network/", org.NetworkSummaryView.as_view(), name="network"),
    path("stores/<int:store_id>/orders/", StoreOrdersView.as_view({"get": "list"}), name="store-orders"),
    path("stores/<int:store_id>/menu-config/", cat.StoreMenuConfigView.as_view(), name="store-menu-config"),
    path("stores/<int:store_id>/availability/update/", cat.StoreAvailabilityView.as_view(),
         name="store-availability-update"),
    path("stores/<int:store_id>/menu-versions/", cat.PublishMenuView.as_view(), name="store-menu-versions"),
    path("stores/<int:store_id>/quote/", cat.QuoteView.as_view(), name="store-quote"),
    path("stores/<int:store_id>/integrations/", integ.StoreIntegrationsView.as_view(), name="store-integrations"),
    path("stores/<int:store_id>/integrations/<str:provider>/", integ.StoreIntegrationDetailView.as_view(),
         name="store-integration"),
    path("stores/<int:store_id>/integrations/<str:provider>/<str:op>/", integ.StoreIntegrationDetailView.as_view(),
         name="store-integration-op"),
    path("integrations/<str:provider>/webhooks/", integ.WebhookReceiverView.as_view(), name="webhook"),
    path("integrations/<str:provider>/webhooks/<str:account_key>/", integ.WebhookReceiverView.as_view(),
         name="webhook-account"),
    path("payments/<uuid:pk>/<str:op>/", orders.PaymentActionsView.as_view(), name="payment-action"),
    path("pos/sync/", orders.OfflineSyncView.as_view(), name="pos-sync"),
    path("reports/summary/", SalesSummaryView.as_view(), name="report-summary"),
    path("audit-logs/", AuditLogView.as_view(), name="audit-logs"),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger"),
    path("redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("", include(router.urls)),
]


def health(request):
    from django.db import connection

    with connection.cursor() as c:
        c.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", health),
    path("api/v1/", include(api_v1)),
]
