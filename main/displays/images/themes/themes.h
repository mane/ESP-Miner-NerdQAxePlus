#pragma once

#include "lvgl.h"

// NerdQAxe+ LTS: only the NerdQaxePlus display theme is exposed.

class Theme {
  protected:
    const lv_img_dsc_t *ui_img_initscreen2;
    const lv_img_dsc_t *ui_img_miningscreen2;
    const lv_img_dsc_t *ui_img_portalscreen;
    const lv_img_dsc_t *ui_img_btcscreen;
    const lv_img_dsc_t *ui_img_settingsscreen;
    const lv_img_dsc_t *ui_img_splashscreen2;
    const lv_img_dsc_t *ui_img_globalStats;

  public:
    const lv_img_dsc_t *getInitscreen2() const { return ui_img_initscreen2; }
    const lv_img_dsc_t *getMiningscreen2() const { return ui_img_miningscreen2; }
    const lv_img_dsc_t *getPortalscreen() const { return ui_img_portalscreen; }
    const lv_img_dsc_t *getBtcscreen() const { return ui_img_btcscreen; }
    const lv_img_dsc_t *getSettingsscreen() const { return ui_img_settingsscreen; }
    const lv_img_dsc_t *getSplashscreen2() const { return ui_img_splashscreen2; }
    const lv_img_dsc_t *getGlobalstats() const { return ui_img_globalStats; }

    void setInitscreen2(const lv_img_dsc_t *img) { ui_img_initscreen2 = img; }
    void setMiningscreen2(const lv_img_dsc_t *img) { ui_img_miningscreen2 = img; }
    void setPortalscreen(const lv_img_dsc_t *img) { ui_img_portalscreen = img; }
    void setBtcscreen(const lv_img_dsc_t *img) { ui_img_btcscreen = img; }
    void setSettingsscreen(const lv_img_dsc_t *img) { ui_img_settingsscreen = img; }
    void setSplashscreen2(const lv_img_dsc_t *img) { ui_img_splashscreen2 = img; }
    void setGlobalstats(const lv_img_dsc_t *img) { ui_img_globalStats = img; }
};

LV_IMG_DECLARE(ui_img_NerdQaxePlus_initscreen2_png);
LV_IMG_DECLARE(ui_img_NerdQaxePlus_miningscreen2_png);
LV_IMG_DECLARE(ui_img_NerdQaxePlus_portalscreen_png);
LV_IMG_DECLARE(ui_img_NerdQaxePlus_btcscreen_png);
LV_IMG_DECLARE(ui_img_NerdQaxePlus_settingsscreen_png);
LV_IMG_DECLARE(ui_img_NerdQaxePlus_splashscreen2_png);
LV_IMG_DECLARE(ui_img_NerdQaxePlus_globalStats_png);

class ThemeNerdqaxeplus : public Theme {
public:
    ThemeNerdqaxeplus() {
        setInitscreen2(&ui_img_NerdQaxePlus_initscreen2_png);
        setMiningscreen2(&ui_img_NerdQaxePlus_miningscreen2_png);
        setPortalscreen(&ui_img_NerdQaxePlus_portalscreen_png);
        setBtcscreen(&ui_img_NerdQaxePlus_btcscreen_png);
        setSettingsscreen(&ui_img_NerdQaxePlus_settingsscreen_png);
        setSplashscreen2(&ui_img_NerdQaxePlus_splashscreen2_png);
        setGlobalstats(&ui_img_NerdQaxePlus_globalStats_png);
    }
};
