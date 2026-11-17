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
from apps.devices import views as dev
from apps.dispatch import views as disp
from apps.payments.views import CashReportView, CashSessionViewSet, RegisterViewSet, ShiftClosingViewSet
from apps.promotions import views as promo
from apps.reporting.views import AuditLogView, SalesSummaryView

router = DefaultRouter(trailing_slash=True)
router.register("users", acc.UserViewSet, basename="user")
router.register("roles", acc.RoleViewSet, basename="role")
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
router.register("devices", dev.DeviceViewSet, basename="device")
router.register("device-registrations", dev.DeviceRegistrationViewSet, basename="deviceregistration")
router.register("device-alerts", dev.AlertViewSet, basename="devicealert")
router.register("device-profiles", dev.DeviceProfileViewSet, basename="deviceprofile")
router.register("registers", RegisterViewSet, basename="register")
router.register("shift-closings", ShiftClosingViewSet, basename="shiftclosing")
router.register("promotions", promo.PromotionViewSet, basename="promotion")
router.register("promotion-policies", promo.PolicyViewSet, basename="promotionpolicy")
router.register("drivers", disp.DriverViewSet, basename="driver")
router.register("delivery-zones", disp.ZoneViewSet, basename="deliveryzone")
router.register("deliveries", disp.DeliveryViewSet, basename="delivery")


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
    path("catalog/starter-setup/", cat.StarterCatalogView.as_view(), name="catalog-starter-setup"),
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
    path("reports/cash/", CashReportView.as_view(), name="report-cash"),
    path("reports/promotions/", promo.PromotionReportView.as_view(), name="report-promotions"),
    path("reports/drivers/", disp.DriverReportView.as_view(), name="report-drivers"),
    path("stores/<int:store_id>/dispatch/", disp.DispatchBoardView.as_view(), name="dispatch-board"),
    path("stores/<int:store_id>/dispatch/assign-many/", disp.AssignManyView.as_view(), name="dispatch-assign-many"),
    path("driver/me/", disp.DriverMeView.as_view(), name="driver-me"),
    path("driver/deliveries/<int:pk>/", disp.DriverDeliveryView.as_view(), name="driver-delivery"),
    path("driver/deliveries/<int:pk>/<str:op>/", disp.DriverDeliveryView.as_view(), name="driver-delivery-op"),
    path("device-auth/enroll/", dev.EnrollView.as_view(), name="device-enroll"),
    path("device-auth/heartbeat/", dev.HeartbeatView.as_view(), name="device-heartbeat"),
    path("device-auth/config/", dev.DeviceConfigView.as_view(), name="device-config"),
    path("device-auth/rotate/", dev.RotateCredentialView.as_view(), name="device-rotate"),
    path("device-auth/me/", dev.DeviceSelfView.as_view(), name="device-self"),
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
