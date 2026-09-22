include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-hpswitch
PKG_VERSION:=1.0.0
PKG_RELEASE:=1

PKG_LICENSE:=MIT
PKG_LICENSE_FILES:=LICENSE
PKG_MAINTAINER:=CHANGE_ME <CHANGE_ME@example.com>

PKG_SOURCE_URL:=https://github.com/CHANGE_ME/luci-app-hpswitch
PKG_SOURCE_PROTO:=git
PKG_SOURCE_VERSION:=v$(PKG_VERSION)

PKG_BUILD_PARALLEL:=1
PKG_ARCH:=all

TITLE:=HomeProxy master switch page for LuCI
DEPENDS:=+luci-base +rpcd-mod-file

include $(INCLUDE_DIR)/package.mk

define Package/$(PKG_NAME)
  SECTION:=luci
  CATEGORY:=LuCI
  SUBMENU:=3. Applications
  TITLE:=$(TITLE)
  DEPENDS:=$(DEPENDS)
  PKGARCH:=all
  MENU:=1
endef

define Package/$(PKG_NAME)/description
  Adds a "Proxy Switch" page under Services -> HomeProxy in LuCI.

  luci-app-homeproxy ships no global client switch, so turning the proxy on or
  off normally means digging into System -> Startup. This package adds one page
  with three buttons that control both the running state and the boot
  autostart of the HomeProxy service:

    - Full      router + LAN devices transparently proxied
    - Self      router itself only, LAN devices bypass
    - Off       service stopped and disabled at boot
endef

define Build/Configure
endef

define Build/Compile
endef

define Package/$(PKG_NAME)/install
	$(INSTALL_DIR) $(1)/usr/bin
	$(INSTALL_BIN) ./files/usr/bin/hpswitch $(1)/usr/bin/hpswitch

	$(INSTALL_DIR) $(1)/usr/share/rpcd/acl.d
	$(INSTALL_CONF) ./files/usr/share/rpcd/acl.d/$(PKG_NAME).json \
		$(1)/usr/share/rpcd/acl.d/$(PKG_NAME).json

	$(INSTALL_DIR) $(1)/usr/share/luci/menu.d
	$(INSTALL_CONF) ./files/usr/share/luci/menu.d/$(PKG_NAME).json \
		$(1)/usr/share/luci/menu.d/$(PKG_NAME).json

	$(INSTALL_DIR) $(1)/www/luci-static/resources/view/hpswitch
	$(INSTALL_CONF) ./files/www/luci-static/resources/view/hpswitch/switch.js \
		$(1)/www/luci-static/resources/view/hpswitch/switch.js
endef

define Package/$(PKG_NAME)/postinst
#!/bin/sh
if [ -z "$${IPKG_INSTROOT}" ]; then
	rm -f /tmp/luci-indexcache* /tmp/luci-modulecache*
	[ -x /etc/init.d/rpcd ] && /etc/init.d/rpcd restart
fi
exit 0
endef

define Package/$(PKG_NAME)/postrm
#!/bin/sh
if [ -z "$${IPKG_INSTROOT}" ]; then
	rm -f /tmp/luci-indexcache* /tmp/luci-modulecache*
	[ -x /etc/init.d/rpcd ] && /etc/init.d/rpcd restart
fi
exit 0
endef

$(eval $(call BuildPackage,$(PKG_NAME)))
