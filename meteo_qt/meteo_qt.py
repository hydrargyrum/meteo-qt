# Purpose: System tray weather application
# Weather data: http://openweathermap.org
# Author: Dimitrios Glentadakis dglent@free.fr
# License: GPLv3

import logging
import logging.handlers
import os
import platform
import sys
import urllib.request
from functools import partial
from socket import timeout
from lxml import etree
import json
import time
import datetime
import traceback
from io import StringIO

from PyQt5.QtCore import (
    PYQT_VERSION_STR, QT_VERSION_STR, QCoreApplication, QByteArray,
    QLibraryInfo, QLocale, QSettings, Qt, QThread, QTimer, QTranslator,
    pyqtSignal, pyqtSlot, QTime, QSize
)
from PyQt5.QtGui import (
    QColor, QCursor, QFont, QIcon, QImage, QMovie, QPainter, QPixmap,
    QTransform, QTextDocument
)
from PyQt5.QtWidgets import (
    QDialog, QAction, QApplication, QMainWindow, QMenu, QSystemTrayIcon, qApp,
    QVBoxLayout, QHBoxLayout, QLabel, QGridLayout, QGraphicsDropShadowEffect
)

try:
    import qrc_resources
    import settings
    import searchcity
    import conditions
    import about_dlg
except ImportError:
    from meteo_qt import qrc_resources
    from meteo_qt import settings
    from meteo_qt import searchcity
    from meteo_qt import conditions
    from meteo_qt import about_dlg


__version__ = "1.5"


class SystemTrayIcon(QMainWindow):
    units_dico = {
        'metric': '°C',
        'imperial': '°F',
        ' ': '°K'
    }

    def __init__(self, parent=None):
        super(SystemTrayIcon, self).__init__(parent)
        self.settings = QSettings()
        self.cityChangeTimer = QTimer()
        self.cityChangeTimer.timeout.connect(self.update_city_gif)

        self.language = self.settings.value('Language') or ''
        self.temp_decimal_bool = self.settings.value('Decimal') or False
        # initialize the tray icon type in case of first run: issue#42
        self.tray_type = self.settings.value('TrayType') or 'icon&temp'
        self.cond = conditions.WeatherConditions()
        self.temporary_city_status = False
        self.conditions = self.cond.trans
        self.clouds = self.cond.clouds
        self.wind = self.cond.wind
        self.wind_dir = self.cond.wind_direction
        self.wind_codes = self.cond.wind_codes
        self.inerror = False
        self.tentatives = 0
        self.system_icons_dico = {
            '01d': 'weather-clear',
            '01n': 'weather-clear-night',
            '02d': 'weather-few-clouds',
            '02n': 'weather-few-clouds-night',
            '03d': 'weather-clouds',
            '03n': 'weather-clouds-night',
            '04d': 'weather-many-clouds',
            '04n': 'weather-many-clouds',
            '09d': 'weather-showers',
            '09n': 'weather-showers',
            '10d': 'weather-showers-day',
            '10n': 'weather-showers-night',
            '11d': 'weather-storm-day',
            '11n': 'weather-storm-night',
            '13d': 'weather-snow',
            '13n': 'weather-snow',
            '50d': 'weather-fog'
        }
        url_prefix = 'http://api.openweathermap.org/data/2.5'
        self.baseurl = f'{url_prefix}/weather?id='
        self.accurate_url = f'{url_prefix}/find?q='
        self.day_forecast_url = f'{url_prefix}/forecast?id='
        self.forecast6_url = f'{url_prefix}/forecast/daily?id='
        self.wIconUrl = 'http://openweathermap.org/img/w/'
        apikey = self.settings.value('APPID') or ''
        self.appid = '&APPID=' + apikey
        self.forecast_icon_url = self.wIconUrl
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.menu = QMenu()
        self.citiesMenu = QMenu(self.tr('Cities'))
        if os.environ.get('DESKTOP_SESSION') in ['ubuntu', 'budgie-desktop']:
            # Missing left click on Unity environment issue #63
            self.panelAction = QAction(
                QCoreApplication.translate(
                    "Tray context menu",
                    "Toggle Window",
                    "Open/closes the application window"
                ),
                self
            )
            self.panelAction.setIcon(QIcon(':/panel'))
            self.menu.addAction(self.panelAction)
            self.panelAction.triggered.connect(self.showpanel)
        self.tempCityAction = QAction(self.tr('&Temporary city'), self)
        self.refreshAction = QAction(
            QCoreApplication.translate(
                'Action to refresh the weather infos from the server',
                '&Refresh',
                'Systray icon context menu'
            ),
            self
        )
        self.settingsAction = QAction(self.tr('&Settings'), self)
        self.aboutAction = QAction(self.tr('&About'), self)
        self.exitAction = QAction(self.tr('Exit'), self)
        self.exitAction.setIcon(QIcon(':/exit'))
        self.aboutAction.setIcon(QIcon(':/info'))
        self.refreshAction.setIcon(QIcon(':/refresh'))
        self.settingsAction.setIcon(QIcon(':/configure'))
        self.tempCityAction.setIcon(QIcon(':/tempcity'))
        self.citiesMenu.setIcon(QIcon(':/bookmarks'))
        self.menu.addAction(self.settingsAction)
        self.menu.addAction(self.refreshAction)
        self.menu.addMenu(self.citiesMenu)
        self.menu.addAction(self.tempCityAction)
        self.menu.addAction(self.aboutAction)
        self.menu.addAction(self.exitAction)
        self.settingsAction.triggered.connect(self.config)
        self.exitAction.triggered.connect(qApp.quit)
        self.refreshAction.triggered.connect(self.manual_refresh)
        self.aboutAction.triggered.connect(self.about)
        self.tempCityAction.triggered.connect(self.tempcity)
        self.systray = QSystemTrayIcon()
        self.systray.setContextMenu(self.menu)
        self.systray.activated.connect(self.activate)
        self.systray.setIcon(QIcon(':/noicon'))
        self.systray.setToolTip(self.tr('Searching weather data...'))
        self.feels_like_translated = QCoreApplication.translate(
            "The Feels Like Temperature",
            "Feels like",
            "Weather info window"
        )
        self.notification = ''
        self.hPaTrend = 0
        self.trendCities_dic = {}
        self.notifier_id = ''
        self.temp_trend = ''
        self.systray.show()
        # The dictionnary has to be intialized here. If there is an error
        # the program couldn't become functionnal if the dictionnary is
        # reinitialized in the weatherdata method
        self.weatherDataDico = {}
        # The traycolor has to be initialized here for the case when we cannot
        # reach the tray method (case: set the color at first time usage)
        self.traycolor = ''
        self.days_dico = {
            '0': self.tr('Mon'),
            '1': self.tr('Tue'),
            '2': self.tr('Wed'),
            '3': self.tr('Thu'),
            '4': self.tr('Fri'),
            '5': self.tr('Sat'),
            '6': self.tr('Sun')
        }
        self.precipitation = self.cond.rain
        self.wind_direction = self.cond.wind_codes
        self.wind_name_dic = self.cond.wind
        self.clouds_name_dic = self.cond.clouds
        self.beaufort_sea_land = self.cond.beaufort
        self.hpa_indications = self.cond.pressure
        self.uv_risk = self.cond.uv_risk
        self.uv_recommend = self.cond.uv_recommend
        self.doc = QTextDocument()
        self.create_overview()
        self.city = self.settings.value('City') or ''
        self.country = self.settings.value('Country') or ''
        self.id_ = self.settings.value('ID') or ''
        self.current_city_display = f'{self.city}_{self.country}_{self.id_}'
        self.cities_menu()
        self.refresh()

    def shadow_effect(self):
        shadow = QGraphicsDropShadowEffect()
        shadow.setColor(QColor(50, 50, 50, 100))
        shadow.setXOffset(5)
        shadow.setYOffset(5)
        shadow.setBlurRadius(20)
        return shadow

    def create_overview(self):
        self.overviewcitydlg = QDialog()
        self.setCentralWidget(self.overviewcitydlg)
        self.total_layout = QVBoxLayout()

        # ----First part overview day -----
        self.over_layout = QVBoxLayout()
        self.dayforecast_layout = QHBoxLayout()
        self.dayforecast_temp_layout = QHBoxLayout()

        self.city_label = QLabel()
        self.over_layout.addWidget(self.city_label)
        self.icontemp_layout = QHBoxLayout()
        self.icon_label = QLabel()
        self.icontemp_layout.addWidget(self.icon_label)
        self.temp_label = QLabel()
        self.temp_label.setWordWrap(True)
        self.icontemp_layout.addWidget(self.temp_label)
        self.over_layout.addLayout(self.icontemp_layout)
        self.weather_label = QLabel()
        self.weather_label.setWordWrap(True)
        self.icontemp_layout.addWidget(self.weather_label)
        self.icontemp_layout.addStretch()
        self.over_layout.addLayout(self.dayforecast_layout)
        self.over_layout.addLayout(self.dayforecast_temp_layout)
        # ------Second part overview day---------
        self.over_grid = QGridLayout()
        # Feels Like
        self.feels_like_label = QLabel(
            '<font size="3" color=><b>{}</b></font>'.format(
                self.feels_like_translated
            )
        )
        self.feels_like_value = QLabel()
        # Wind
        self.wind_label = QLabel(
            '<font size="3" color=><b>{}</b></font>'.format(
                QCoreApplication.translate(
                    'Label before the wind description',
                    'Wind',
                    'Weather info panel'
                )
            )
        )
        self.wind_label.setAlignment(Qt.AlignTop)
        self.windLabelDescr = QLabel('None')
        self.wind_icon_label = QLabel()
        self.wind_icon_label.setAlignment(Qt.AlignLeft)
        self.wind_icon = QPixmap(':/arrow')
        # Clouds
        self.clouds_label = QLabel(
            '<font size="3" color=><b>{}</b></font>'.format(
                QCoreApplication.translate(
                    'Label for the cloudiness (%)',
                    'Cloudiness',
                    'Weather info panel'
                )
            )
        )
        self.clouds_name = QLabel()

        # Pressure
        self.pressure_label = QLabel(
            '<font size="3" color=><b>{}</b></font>'.format(
                QCoreApplication.translate(
                    'Label for the pressure (hPa)',
                    'Pressure',
                    'Weather info panel'
                )
            )
        )
        self.pressure_value = QLabel()

        # Humidity
        self.humidity_label = QLabel(
            '<font size="3" color=><b>{}</b></font>'.format(
                QCoreApplication.translate(
                    'Label for the humidity (%)',
                    'Humidity',
                    'Weather info panel'
                )
            )
        )
        self.humidity_value = QLabel()
        # Precipitation
        self.precipitation_label = QLabel(
            '<font size="3" color=><b>{}</b></font>'.format(
                QCoreApplication.translate(
                    'Precipitation type (no/rain/snow)',
                    'Precipitation',
                    'Weather overview dialogue'
                )
            )
        )
        self.precipitation_value = QLabel()
        # Sunrise Sunset Daylight
        self.sunrise_label = QLabel(
            '<font color=><b>{}</b></font>'.format(
                QCoreApplication.translate(
                    'Label for the sunrise time (hh:mm)',
                    'Sunrise',
                    'Weather info panel'
                )
            )
        )
        self.sunset_label = QLabel(
            '<font color=><b>{}</b></font>'.format(
                QCoreApplication.translate(
                    'Label for the sunset (hh:mm)',
                    'Sunset',
                    'Weather info panel'
                )
            )
        )
        self.sunrise_value = QLabel()
        self.sunset_value = QLabel()
        self.daylight_label = QLabel(
            '<font color=><b>{}</b></font>'.format(
                QCoreApplication.translate(
                    'Daylight duration',
                    'Daylight',
                    'Weather overview dialogue'
                )
            )
        )
        self.daylight_value_label = QLabel()
        # --UV---
        self.uv_label = QLabel(
            '<font size="3" color=><b>{}</b></font>'.format(
                QCoreApplication.translate(
                    'Ultraviolet index',
                    'UV',
                    'Label in weather info dialogue'
                )
            )
        )
        self.uv_label.setAlignment(Qt.AlignTop)
        self.uv_value_label = QLabel()
        # Ozone
        self.ozone_label = QLabel(
            '<font size="3" color=><b>{}</b></font>'.format(
                QCoreApplication.translate(
                    'Ozone data title',
                    'Ozone',
                    'Label in weather info dialogue'
                )
            )
        )
        self.ozone_value_label = QLabel()

        self.over_grid.addWidget(self.feels_like_label, 0, 0)
        self.over_grid.addWidget(self.feels_like_value, 0, 1)
        self.over_grid.addWidget(self.wind_label, 1, 0)
        self.over_grid.addWidget(self.windLabelDescr, 1, 1)
        self.over_grid.addWidget(self.wind_icon_label, 1, 2)
        self.over_grid.addWidget(self.clouds_label, 2, 0)
        self.over_grid.addWidget(self.clouds_name, 2, 1)
        self.over_grid.addWidget(self.pressure_label, 3, 0)
        self.over_grid.addWidget(self.pressure_value, 3, 1)
        self.over_grid.addWidget(self.humidity_label, 4, 0)
        self.over_grid.addWidget(self.humidity_value, 4, 1, 1, 3)  # align left
        self.over_grid.addWidget(self.precipitation_label, 5, 0)
        self.over_grid.addWidget(self.precipitation_value, 5, 1)
        self.over_grid.addWidget(self.sunrise_label, 6, 0)
        self.over_grid.addWidget(self.sunrise_value, 6, 1)
        self.over_grid.addWidget(self.sunset_label, 7, 0)
        self.over_grid.addWidget(self.sunset_value, 7, 1)
        self.over_grid.addWidget(self.daylight_label, 8, 0)
        self.over_grid.addWidget(self.daylight_value_label, 8, 1)
        self.over_grid.addWidget(self.uv_label, 9, 0)
        self.over_grid.addWidget(self.uv_value_label, 9, 1)
        # # -------------Forecast-------------
        self.forecast_days_layout = QHBoxLayout()
        self.forecast_icons_layout = QHBoxLayout()
        self.forecast_minmax_layout = QHBoxLayout()
        # ----------------------------------
        self.total_layout.addLayout(self.over_layout)
        self.total_layout.addLayout(self.over_grid)
        self.total_layout.addLayout(self.forecast_icons_layout)
        self.total_layout.addLayout(self.forecast_days_layout)
        self.total_layout.addLayout(self.forecast_minmax_layout)

        self.overviewcitydlg.setLayout(self.total_layout)
        self.setWindowTitle(self.tr('Weather status'))

    def overviewcity(self):
        self.forecast_weather_list = []
        self.dayforecast_weather_list = []
        self.icon_list = []
        self.dayforecast_icon_list = []
        self.unit_temp = self.units_dico[self.unit]
        # ----First part overview day -----

        # Check for city translation
        cities_trans = self.settings.value('CitiesTranslation') or '{}'
        cities_trans_dict = eval(cities_trans)
        city_notrans = (
            '{0}_{1}_{2}'.format(
                self.weatherDataDico['City'],
                self.weatherDataDico['Country'],
                self.weatherDataDico['Id']
            )
        )
        if city_notrans in cities_trans_dict:
            city_label = cities_trans_dict[city_notrans]
        else:
            city_label = (
                '{0}, {1}'.format(
                    self.weatherDataDico['City'],
                    self.weatherDataDico['Country']
                )
            )
        self.city_label.setText(
            f'<font size="4"><b>{city_label}</b></font>'
        )

        self.icon_label.setPixmap(self.wIcon)
        self.system_icons = self.settings.value('SystemIcons') or 'False'
        if self.system_icons == 'True':
            shadow = self.shadow_effect()
            self.icon_label.setGraphicsEffect(shadow)

        self.temp_label.setText(
            '<font size="5"><b>{0} {1}{2}</b></font>'.format(
                '{0:.1f}'.format(float(self.weatherDataDico['Temp'][:-1])),
                self.unit_temp,
                self.temp_trend
            )
        )
        self.weather_label.setText(
            f'<font size="3"><b>{self.weatherDataDico["Meteo"]}</b></font>'
        )
        self.feels_like_value.setText(
            '{0} {1}'.format(
                self.weatherDataDico['Feels_like'][0],
                self.weatherDataDico['Feels_like'][1]
            )
        )

        # Wind
        wind_unit = self.settings.value('Unit') or 'metric'
        wind_unit_speed_config = self.settings.value('Wind_unit') or 'df'
        if wind_unit_speed_config == 'bf':
            self.bft_bool = True
        else:
            self.bft_bool = False
        self.unit_system = ' m/s '
        self.unit_system_wind = ' m/s '
        if wind_unit == 'imperial':
            self.unit_system = ' mph '
            self.unit_system_wind = ' mph '

        wind_speed = '{0:.1f}'.format(float(self.weatherDataDico['Wind'][0]))
        windTobeaufort = str(self.convertToBeaufort(wind_speed))

        if self.bft_bool is True:
            wind_speed = windTobeaufort
            self.unit_system_wind = ' Bft. '

        if wind_unit == 'metric' and wind_unit_speed_config == 'km':
            self.wind_km_bool = True
            wind_speed = '{0:.1f}'.format(float(wind_speed) * 3.6)
            self.unit_system_wind = QCoreApplication.translate(
                '''Unit displayed after the wind speed value and before
                the wind description (keep the spaces before and after)''',
                ' km/h ',
                'Weather Infos panel'
            )
        else:
            self.wind_km_bool = False

        try:
            self.windLabelDescr.setText(
                '<font color=>{0} {1}° <br/>{2}{3}{4}</font>'.format(
                    self.weatherDataDico['Wind'][4],
                    self.weatherDataDico['Wind'][2],
                    wind_speed,
                    self.unit_system_wind,
                    self.weatherDataDico['Wind'][1]
                )
            )
            self.windLabelDescr.setToolTip(
                self.beaufort_sea_land[windTobeaufort]
            )
        except:
            logging.error(
                'Cannot find wind informations:\n{}'.format(
                    self.weatherDataDico['Wind']
                )
            )

        self.wind_icon_direction()

        # Clouds
        self.clouds_name.setText(
            f'<font color=>{self.weatherDataDico["Clouds"]}</font>'
        )

        # Pressure
        if self.hPaTrend == 0:
            hpa = "→"
        elif self.hPaTrend < 0:
            hpa = "↘"
        elif self.hPaTrend > 0:
            hpa = "↗"
        self.pressure_value.setText(
            '<font color=>{0} {1} {2}</font>'.format(
                str(float(self.weatherDataDico['Pressure'][0])),
                self.weatherDataDico['Pressure'][1],
                hpa
            )
        )
        self.pressure_value.setToolTip(self.hpa_indications['hpa'])
        # Humidity
        self.humidity_value.setText(
            '<font color=>{0} {1}</font>'.format(
                self.weatherDataDico['Humidity'][0],
                self.weatherDataDico['Humidity'][1]
            )
        )
        # Precipitation
        rain_mode = (
            self.precipitation[self.weatherDataDico['Precipitation'][0]]
        )
        rain_value = self.weatherDataDico['Precipitation'][1]
        rain_unit = ' mm '
        if rain_value == '':
            rain_unit = ''
        else:
            if wind_unit == 'imperial':
                rain_unit = 'inch'
                rain_value = str(float(rain_value) / 25.4)
                rain_value = "{0:.4f}".format(float(rain_value))
            else:
                rain_value = "{0:.2f}".format(float(rain_value))
        self.precipitation_value.setText(
            '<font color=>{0} {1} {2}</font>'.format(
                rain_mode,
                rain_value,
                rain_unit
            )
        )
        # Sunrise Sunset Daylight
        try:
            rise_str = self.utc('Sunrise', 'weatherdata')
            set_str = self.utc('Sunset', 'weatherdata')
        except (AttributeError, ValueError):
            logging.error('Cannot find sunrise, sunset time info')
            # if value is None
            rise_str = '00:00:00'
            set_str = '00:00:00'

        self.sunrise_value.setText(
            f'<font color=>{rise_str[:-3]}</font>'
        )
        self.sunset_value.setText(
            f'<font color=>{set_str[:-3]}</font>'
        )

        daylight_value = self.daylight_delta(rise_str[:-3], set_str[:-3])
        self.daylight_value_label.setText(
            f'<font color=>{daylight_value}</font>'
        )
        # --UV---
        fetching_text = (
            '<font color=>{}</font>'.format(
                QCoreApplication.translate(
                    'Ultraviolet index waiting text label',
                    'Fetching...',
                    'Weather info dialogue'
                )
            )
        )
        self.uv_value_label.setText(fetching_text)
        # Ozone
        self.ozone_value_label.setText(fetching_text)

        if self.forcast6daysBool:
            self.forecast6data()
        else:
            self.forecastdata()
        self.iconfetch()
        logging.debug('Fetched 6 days forecast icons')
        self.dayforecastdata()
        logging.debug('Fetched day forecast data')
        self.dayiconfetch()
        logging.debug('Fetched day forcast icons')
        self.uv_fetch()
        logging.debug('Fetched uv index')
        self.ozone_fetch()
        logging.debug('Fetched ozone data')

        self.restoreGeometry(
            self.settings.value(
                "MainWindow/Geometry",
                QByteArray()
            )
        )
        # Option to start with the panel closed, true by defaut
        # starting with the panel open can be useful for users who don't have plasma
        # installed (to set keyboard shortcuts or other default window behaviours)
        start_minimized = self.settings.value('StartMinimized') or 'True'
        if start_minimized == 'False':
            self.showpanel()

    def daylight_delta(self, s1, s2):
        FMT = '%H:%M'
        tdelta = (
            datetime.datetime.strptime(s2, FMT)
            - datetime.datetime.strptime(s1, FMT)
        )
        m, s = divmod(tdelta.seconds, 60)
        h, m = divmod(m, 60)
        if len(str(m)) == 1:
            m = f'0{str(m)}'
        daylight_in_hours = f'{str(h)}:{str(m)}'
        return daylight_in_hours

    def utc(self, rise_set, what):
        ''' Convert sun rise/set from UTC to local time
            'rise_set' is 'Sunrise' or 'Sunset' when it is for weatherdata
            or the index of hour in day forecast when dayforecast'''
        strtime = ''
        if what == 'weatherdata':
            strtime = (
                self.weatherDataDico[rise_set].split('T')[1]
            )
        elif what == 'dayforecast':
            if not self.json_data_bool:
                strtime = (
                    self.dayforecast_data[4][rise_set].get('from')
                    .split('T')[1]
                )
            else:
                strtime = (
                    self.dayforecast_data['list'][rise_set]['dt_txt'][10:]
                )

        suntime = QTime.fromString(strtime)

        # add the diff UTC-local in seconds
        utc_time = suntime.addSecs(time.localtime().tm_gmtoff)
        utc_time_str = utc_time.toString()
        return utc_time_str

    def convertToBeaufort(self, speed):
        speed = float(speed)
        if self.unit_system.strip() == 'm/s':
            if speed <= 0.2:
                return 0
            elif speed <= 1.5:
                return 1
            elif speed <= 3.3:
                return 2
            elif speed <= 5.4:
                return 3
            elif speed <= 7.9:
                return 4
            elif speed <= 10.7:
                return 5
            elif speed <= 13.8:
                return 6
            elif speed <= 17.1:
                return 7
            elif speed <= 20.7:
                return 8
            elif speed <= 24.4:
                return 9
            elif speed <= 28.4:
                return 10
            elif speed <= 32.4:
                return 11
            elif speed <= 36.9:
                return 12
        elif self.unit_system.strip() == 'mph':
            if speed < 1:
                return 0
            elif speed < 4:
                return 1
            elif speed < 8:
                return 2
            elif speed < 13:
                return 3
            elif speed < 18:
                return 4
            elif speed < 25:
                return 5
            elif speed < 32:
                return 6
            elif speed < 39:
                return 7
            elif speed < 47:
                return 8
            elif speed < 55:
                return 9
            elif speed < 64:
                return 10
            elif speed < 73:
                return 11
            elif speed <= 82:
                return 12

    def wind_icon_direction(self):
        angle = self.weatherDataDico['Wind'][2]
        if angle == '':
            if self.wind_icon_label.isVisible is True:
                self.wind_icon_label.hide()
            return
        else:
            if self.wind_icon_label.isVisible is False:
                self.wind_icon_label.show()
        transf = QTransform()
        logging.debug(f'Wind degrees direction: {angle}')
        transf.rotate(int(float(angle)))
        rotated = self.wind_icon.transformed(
            transf, mode=Qt.SmoothTransformation
        )
        self.wind_icon_label.setPixmap(rotated)

    def ozone_du(self, du):
        if du <= 125:
            return '#060106'  # black
        elif du <= 150:
            return '#340634'  # magenta
        elif du <= 175:
            return '#590b59'  # fuccia
        elif du <= 200:
            return '#421e85'  # violet
        elif du <= 225:
            return '#121e99'  # blue
        elif du <= 250:
            return '#125696'  # blue sea
        elif du <= 275:
            return '#198586'  # raf
        elif du <= 300:
            return '#21b1b1'  # cyan
        elif du <= 325:
            return '#64b341'  # light green
        elif du <= 350:
            return '#1cac1c'  # green
        elif du <= 375:
            return '#93a92c'  # green oil
        elif du <= 400:
            return '#baba2b'  # yellow
        elif du <= 425:
            return '#af771f'  # orange
        elif du <= 450:
            return '#842910'  # brown
        elif du <= 475:
            return '#501516'  # brown dark
        elif du > 475:
            return '#210909'  # darker brown

    def uv_color(self, uv):
        try:
            uv = float(uv)
        except:
            return ('grey', 'None')
        if uv <= 2.99:
            return ('green', 'Low')
        elif uv <= 5.99:
            return ('gold', 'Moderate')
        elif uv <= 7.99:
            return ('orange', 'High')
        elif uv <= 10.99:
            return ('red', 'Very high')
        elif uv >= 11:
            return ('purple', 'Extreme')

    def winddir_json_code(self, deg):
        deg = float(deg)
        if deg < 22.5 or deg > 337.5:
            return 'N'
        elif deg < 45:
            return 'NNE'
        elif deg < 67.5:
            return 'NE'
        elif deg < 90:
            return 'ENE'
        elif deg < 112.5:
            return 'E'
        elif deg < 135:
            return 'ESE'
        elif deg < 157.5:
            return 'SE'
        elif deg < 180:
            return 'SSE'
        elif deg < 202.5:
            return 'S'
        elif deg < 225:
            return 'SSW'
        elif deg < 247.5:
            return 'SW'
        elif deg < 270:
            return 'WSW'
        elif deg < 292.5:
            return 'W'
        elif deg < 315:
            return 'WNW'
        elif deg <= 337.5:
            return 'NNW'

    def find_min_max(self, fetched_file_periods):
        ''' Collate the temperature of each forecast time
            to find the min max T° of the forecast
            of the day in the 4 days forecast '''
        self.date_temp_forecast = {}
        for element in self.dayforecast_data.iter():
            if element.tag == 'time':
                date_list = element.get('from').split('-')
                date_list_time = date_list[2].split('T')
                date_list[2] = date_list_time[0]
            if element.tag == 'temperature':
                if not date_list[2] in self.date_temp_forecast:
                    self.date_temp_forecast[date_list[2]] = []
                self.date_temp_forecast[date_list[2]].append(
                    float(element.get('max')))

    def forecast6data(self):
        '''Forecast for the next 6 days'''
        # Some times server sends less data
        self.clearLayout(self.forecast_minmax_layout)
        self.clearLayout(self.forecast_days_layout)
        periods = 7
        fetched_file_periods = (len(self.forecast6_data.xpath('//time')))
        if fetched_file_periods < periods:
            periods = fetched_file_periods
            logging.warning(
                'Reduce forecast for the next 6 days to {0}'.format(
                    periods - 1
                )
            )
        counter_day = 0
        forecast_data = False

        for element in self.forecast6_data.iter():

            if element.tag == 'time':
                forecast_data = True
            if forecast_data is False:
                continue

            if element.tag == 'time':
                counter_day += 1
                if counter_day == periods:
                    break

                weather_end = False
                date_list = element.get('day').split('-')
                day_of_week = str(datetime.date(
                    int(date_list[0]), int(date_list[1]),
                    int(date_list[2])).weekday()
                )
                label = QLabel(f'{self.days_dico[day_of_week]}')
                label.setToolTip(element.get('day'))
                label.setAlignment(Qt.AlignHCenter)
                self.forecast_days_layout.addWidget(label)

            if element.tag == 'temperature':
                mlabel = QLabel(
                    '<font color=>{0}°<br/>{1}°</font>'.format(
                        '{0:.0f}'.format(float(element.get('min'))),
                        '{0:.0f}'.format(float(element.get('max')))
                    )
                )
                mlabel.setAlignment(Qt.AlignHCenter)
                mlabel.setToolTip(self.tr('Min Max Temperature of the day'))
                self.forecast_minmax_layout.addWidget(mlabel)

            if element.tag == 'symbol':
                # icon
                self.icon_list.append(element.get('var'))
                weather_cond = element.get('name')
                try:
                    weather_cond = (
                        self.conditions[element.get('number')]
                    )
                except KeyError:
                    logging.warning(
                        f'Cannot find localisation string for: {weather_cond}'
                    )
                    pass

            if element.tag == 'feels_like':
                feels_like_day = element.get('day')
                feels_like_morning = element.get('morn')
                feels_like_night = element.get('night')
                feels_like_eve = element.get('eve')
                feels_like_unit = element.get('unit')
                if feels_like_unit == 'celsius':
                    feels_like_unit = '°C'
                else:
                    feels_like_unit = '°F'
                feels_like_day_label = QCoreApplication.translate(
                    'Tooltip on weather icon on 6 days forecast',
                    'Day',
                    'Weather information window'
                )
                feels_like_morning_label = QCoreApplication.translate(
                    'Tooltip on weather icon on 6 days forecast',
                    'Morning',
                    'Weather information window'
                )
                feels_like_eve_label = QCoreApplication.translate(
                    'Tooltip on weather icon on 6 days forecast',
                    'Evening',
                    'Weather information window'
                )
                feels_like_night_label = QCoreApplication.translate(
                    'Tooltip on weather icon on 6 days forecast',
                    'Night',
                    'Weather information window'
                )
                weather_cond += (
                    f'\n―――――\n{self.feels_like_translated} \n'
                    f'{feels_like_morning_label} {feels_like_morning} {feels_like_unit}\n'
                    f'{feels_like_day_label} {feels_like_day} {feels_like_unit}\n'
                    f'{feels_like_eve_label} {feels_like_eve} {feels_like_unit}\n'
                    f'{feels_like_night_label} {feels_like_night} {feels_like_unit}\n'
                    '―――――'
                )

            if element.tag == 'precipitation':

                try:
                    # Take the label translated text and remove the html tags
                    self.doc.setHtml(self.precipitation_label.text())
                    precipitation_label = f'{self.doc.toPlainText()}: '
                    precipitation_type = element.get('type')
                    precipitation_type = (
                        f'{self.precipitation[precipitation_type]} '
                    )
                    precipitation_value = element.get('value')
                    rain_unit = ' mm'
                    if self.unit_system == ' mph ':
                        rain_unit = ' inch'
                        precipitation_value = (
                            f'{str(float(precipitation_value) / 25.4)} '
                        )
                        precipitation_value = (
                            "{0:.2f}".format(float(precipitation_value))
                        )
                    else:
                        precipitation_value = (
                            "{0:.1f}".format(float(precipitation_value))
                        )
                    weather_cond += (
                        '\n{0}{1}{2}{3}'.format(
                            precipitation_label,
                            precipitation_type,
                            precipitation_value,
                            rain_unit
                        )
                    )
                except:
                    pass

            if element.tag == 'windDirection':
                self.doc.setHtml(self.wind_label.text())
                wind = f'{self.doc.toPlainText()}: '
                try:
                    wind_direction = (
                        self.wind_direction[element.get('code')]
                    )
                except KeyError:
                    wind_direction = ''

            if element.tag == 'windSpeed':
                wind_speed = (
                    '{0:.1f}'.format(float(element.get('mps')))
                )
                if self.bft_bool:
                    wind_speed = str(self.convertToBeaufort(wind_speed))
                if self.wind_km_bool:
                    wind_speed = '{0:.1f}'.format(float(wind_speed) * 3.6)

                weather_cond += (
                    '\n{0}{1}{2}{3}'.format(
                        wind,
                        wind_speed,
                        self.unit_system_wind,
                        wind_direction
                    )
                )

            if element.tag == 'pressure':

                self.doc.setHtml(self.pressure_label.text())
                pressure_label = f'{self.doc.toPlainText()}: '
                pressure = (
                    '{0:.1f}'.format(
                        float(element.get('value'))
                    )
                )
                weather_cond += f'\n{pressure_label}{pressure} hPa'

            if element.tag == 'humidity':
                humidity = element.get('value')
                self.doc.setHtml(self.humidity_label.text())
                humidity_label = f'{self.doc.toPlainText()}: '
                weather_cond += f'\n{humidity_label}{humidity} %'

            if element.tag == 'clouds':
                clouds = element.get('all')
                self.doc.setHtml(self.clouds_label.text())
                clouds_label = f'{self.doc.toPlainText()}: '
                weather_cond += f'\n{clouds_label}{clouds} %'
                weather_end = True

            if weather_end is True:
                self.forecast_weather_list.append(weather_cond)
                weather_end = False

    def forecastdata(self):
        '''Forecast for the next 4 days'''
        # Some times server sends less data
        self.clearLayout(self.forecast_minmax_layout)
        self.clearLayout(self.forecast_days_layout)
        fetched_file_periods = (len(self.dayforecast_data.xpath('//time')))
        self.find_min_max(fetched_file_periods)
        weather_end = False
        collate_info = False
        for element in self.dayforecast_data.iter():
            # Find the day for the forecast (today+1) at 12:00:00
            if element.tag == 'time':
                date_list = element.get('from').split('-')
                date_list_time = date_list[2].split('T')
                date_list[2] = date_list_time[0]
                date_list.append(date_list_time[1])
                if (
                    datetime.datetime.now().day == int(date_list[2])
                    or date_list[3] != '12:00:00'
                ):
                    collate_info = False
                    continue
                else:
                    collate_info = True
                day_of_week = str(
                    datetime.date(
                        int(date_list[0]),
                        int(date_list[1]),
                        int(date_list[2])
                    ).weekday()
                )

                label = QLabel(f'{self.days_dico[day_of_week]}')
                label.setToolTip('-'.join(i for i in date_list[:3]))
                label.setAlignment(Qt.AlignHCenter)
                self.forecast_days_layout.addWidget(label)
                temp_min = min(self.date_temp_forecast[date_list[2]])
                temp_max = max(self.date_temp_forecast[date_list[2]])
                mlabel = QLabel(
                    '<font color=>{0}°<br/>{1}°</font>'.format(
                        '{0:.0f}'.format(temp_min),
                        '{0:.0f}'.format(temp_max)
                    )
                )
                mlabel.setAlignment(Qt.AlignHCenter)
                mlabel.setToolTip(self.tr('Min Max Temperature of the day'))
                self.forecast_minmax_layout.addWidget(mlabel)

            if element.tag == 'symbol' and collate_info:
                # icon
                self.icon_list.append(element.get('var'))
                weather_cond = element.get('name')
                try:
                    weather_cond = (
                        self.conditions[
                            element.get('number')
                        ]
                    )
                except:
                    logging.warning(
                        f'Cannot find localisation string for: {weather_cond}'
                    )
                    pass
            if element.tag == 'precipitation' and collate_info:
                try:
                    # Take the label translated text and remove the html tags
                    self.doc.setHtml(self.precipitation_label.text())
                    precipitation_label = f'{self.doc.toPlainText()}: '
                    precipitation_type = element.get('type')
                    precipitation_type = (
                        f'{self.precipitation[precipitation_type]} '
                    )
                    precipitation_value = (
                        element.get('value')
                    )
                    rain_unit = ' mm'
                    if self.unit_system == ' mph ':
                        rain_unit = ' inch'
                        precipitation_value = (
                            f'{str(float(precipitation_value) / 25.4)} '
                        )
                        precipitation_value = (
                            "{0:.2f}".format(float(precipitation_value))
                        )
                    else:
                        precipitation_value = (
                            "{0:.1f}".format(float(precipitation_value))
                        )
                    weather_cond += (
                        '\n{0}{1}{2}{3}'.format(
                            precipitation_label,
                            precipitation_type,
                            precipitation_value,
                            rain_unit
                        )
                    )
                except:
                    pass

                self.doc.setHtml(self.wind_label.text())
                wind = f'{self.doc.toPlainText()}: '

            if element.tag == 'windDirection' and collate_info:
                try:
                    wind_direction = (
                        self.wind_direction[
                            element.get('code')
                        ]
                    )
                except:
                    wind_direction = ''

            if element.tag == 'windSpeed' and collate_info:
                wind_speed = (
                    '{0:.1f}'.format(
                        float(element.get('mps'))
                    )
                )
                if self.bft_bool:
                    wind_speed = str(self.convertToBeaufort(wind_speed))
                if self.wind_km_bool:
                    wind_speed = '{0:.1f}'.format(float(wind_speed) * 3.6)
                weather_cond += (
                    '\n{0}{1}{2}{3}'.format(
                        wind,
                        wind_speed,
                        self.unit_system_wind,
                        wind_direction
                    )
                )
            if element.tag == 'feels_like' and collate_info:
                feels_like_value = element.get('value')
                feels_like_unit = element.get('unit')
                if feels_like_unit == 'celsius':
                    feels_like_unit = '°C'
                if feels_like_unit == 'fahrenheit':
                    feels_like_unit = '°F'
                weather_cond += f'\n{self.feels_like_translated} : {feels_like_value} {feels_like_unit}'

            if element.tag == 'pressure' and collate_info:
                self.doc.setHtml(self.pressure_label.text())
                pressure_label = f'{self.doc.toPlainText()}: '
                pressure = (
                    '{0:.1f}'.format(
                        float(
                            element.get('value')
                        )
                    )
                )
                weather_cond += f'\n{pressure_label}{pressure} hPa'

            if element.tag == 'humidity' and collate_info:
                humidity = element.get('value')
                self.doc.setHtml(self.humidity_label.text())
                humidity_label = f'{self.doc.toPlainText()}: '
                weather_cond += f'\n{humidity_label}{humidity} %'

            if element.tag == 'clouds' and collate_info:
                clouds = element.get('all')
                self.doc.setHtml(self.clouds_label.text())
                clouds_label = f'{self.doc.toPlainText()}: '
                weather_cond += f'\n{clouds_label}{clouds} %'
                weather_end = True

            if weather_end is True:
                self.forecast_weather_list.append(weather_cond)
                weather_end = False

    def iconfetch(self):
        '''Get icons for the next days forecast'''
        self.clearLayout(self.forecast_icons_layout)
        logging.debug('Download forecast icons...')
        self.system_icons = self.settings.value('SystemIcons') or 'False'
        if self.system_icons == 'True':
            for icon in self.icon_list:
                logging.debug(
                    f'Use the system icon "{self.system_icons_dico.get(icon, False)}" '
                    f'for the openweathermap icon "{icon}"'
                )
                image = QIcon.fromTheme(self.system_icons_dico[icon])
                if image.name() == '':
                    logging.critical(
                        f"The icon {self.system_icons_dico.get(icon, False)} "
                        f"doesn't exist in the system icons theme {QIcon.themeName()}"
                    )

                iconlabel = QLabel()
                iconlabel.setAlignment(Qt.AlignHCenter)
                iconpixmap = image.pixmap(QSize(50, 50))
                iconlabel.setPixmap(iconpixmap)
                shadow = self.shadow_effect()
                iconlabel.setGraphicsEffect(shadow)
                try:
                    iconlabel.setToolTip(self.forecast_weather_list.pop(0))
                    self.forecast_icons_layout.addWidget(iconlabel)
                except IndexError as error:
                    logging.error(f'{str(error)} forecast_weather_list')
        else:
            self.download_thread = (
                IconDownload(self.forecast_icon_url, self.icon_list)
            )
            self.download_thread.wimage['PyQt_PyObject'].connect(self.iconwidget)
            self.download_thread.url_error_signal['QString'].connect(self.errorIconFetch)
            self.download_thread.start()

    def clearLayout(self, layout):
        if layout is not None:
            while layout.count():
                item = layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
                else:
                    self.clearLayout(item.layout())

    def iconwidget(self, icon):
        '''Next days forecast icons'''
        image = QImage()
        image.loadFromData(icon)
        iconlabel = QLabel()
        iconlabel.setAlignment(Qt.AlignHCenter)
        iconpixmap = QPixmap(image)
        iconlabel.setPixmap(iconpixmap)
        try:
            iconlabel.setToolTip(self.forecast_weather_list.pop(0))
            self.forecast_icons_layout.addWidget(iconlabel)
        except IndexError as error:
            logging.error(f'{str(error)} forecast_weather_list')
            return

    def dayforecastdata(self):
        '''Fetch forecast for the day'''
        self.clearLayout(self.dayforecast_temp_layout)
        periods = 6
        start = 0
        if not self.json_data_bool:
            start = 1
            periods = 7
            fetched_file_periods = (len(self.dayforecast_data.xpath('//time')))
            if fetched_file_periods < periods:
                # Some times server sends less data
                periods = fetched_file_periods
                logging.warning(
                    'Reduce forecast of the day to {0}'.format(periods - 1)
                )
        for d in range(start, periods):
            clouds_translated = ''
            wind = ''
            timeofday = self.utc(d, 'dayforecast')
            if not self.json_data_bool:
                weather_cond = self.conditions[
                    self.dayforecast_data[4][d][0].get('number')
                ]
                self.dayforecast_icon_list.append(
                    self.dayforecast_data[4][d][0].get('var')
                )
                temperature_at_hour = float(
                    self.dayforecast_data[4][d][4].get('value')
                )
                feels_like_value = self.dayforecast_data[4][d][5].get('value')
                feels_like_unit_dic = {'celsius': '°C', 'fahrenheit': '°F'}
                feels_like_unit = feels_like_unit_dic[self.dayforecast_data[4][d][5].get('unit')]

                precipitation = str(
                    self.dayforecast_data[4][d][1].get('value')
                )
                precipitation_type = str(
                    self.dayforecast_data[4][d][1].get('type')
                )
                windspeed = self.dayforecast_data[4][d][3].get('mps')
                winddircode = self.dayforecast_data[4][d][2].get('code')
                wind_name = self.dayforecast_data[4][d][3].get('name')
                try:
                    wind_name_translated = (
                        f'{self.conditions[self.wind_name_dic[wind_name.lower()]]}<br/>'
                    )
                    wind += wind_name_translated
                except KeyError:
                    logging.warning(f'Cannot find wind name: {str(wind_name)}')
                    logging.info('Set wind name to None')
                    wind = ''
                finally:
                    if wind == '':
                        wind += '<br/>'
                pressure = self.dayforecast_data[4][d][6].get('value')
                humidity = self.dayforecast_data[4][d][7].get('value')
                clouds = self.dayforecast_data[4][d][8].get('value')
                cloudspercent = self.dayforecast_data[4][d][8].get('all')
            else:
                weather_cond = self.conditions[
                    str(self.dayforecast_data['list'][d]['weather'][0]['id'])
                ]
                self.dayforecast_icon_list.append(
                    self.dayforecast_data['list'][d]['weather'][0]['icon']
                )
                temperature_at_hour = float(
                    self.dayforecast_data['list'][d]['main']['temp']
                )
                precipitation_orig = self.dayforecast_data['list'][d]
                precipitation_rain = precipitation_orig.get('rain')
                precipitation_snow = precipitation_orig.get('snow')
                if (
                    precipitation_rain is not None
                    and len(precipitation_rain) > 0
                ):
                    precipitation_type = 'rain'
                    precipitation = precipitation_rain['3h']
                elif (
                    precipitation_snow is not None
                    and len(precipitation_snow) > 0
                ):
                    precipitation_type = 'snow'
                    precipitation_snow['3h']
                else:
                    precipitation = 'None'
                windspeed = self.dayforecast_data['list'][d]['wind']['speed']
                winddircode = (
                    self.winddir_json_code(
                        self.dayforecast_data['list'][d]['wind'].get('deg')
                    )
                )
                clouds = (
                    self.dayforecast_data['list']
                    [d]['weather'][0]['description']
                )
                cloudspercent = (
                    self.dayforecast_data['list'][0]['clouds']['all']
                )

            self.dayforecast_weather_list.append(weather_cond)
            daytime = QLabel(
                '<font color=>{0}<br/>{1}°</font>'.format(
                    timeofday[:-3],
                    '{0:.0f}'.format(temperature_at_hour)
                )
            )
            daytime.setAlignment(Qt.AlignHCenter)
            unit = self.settings.value('Unit') or 'metric'
            if unit == 'metric':
                mu = 'mm'
                if precipitation.count('None') == 0:
                    precipitation = "{0:.1f}".format(float(precipitation))
            elif unit == 'imperial':
                mu = 'inch'
                if precipitation.count('None') == 0:
                    precipitation = str(float(precipitation) / 25.0)
                    precipitation = "{0:.2f}".format(float(precipitation))
            elif unit == ' ':
                mu = 'kelvin'
            ttip = (
                f'{self.feels_like_translated} '
                f'{feels_like_value} {feels_like_unit}'
                '<br/>'
            )
            ttip_prec = (
                f'{str(precipitation)} {mu} {precipitation_type}<br/>'
            )
            if ttip_prec.count('None') >= 1:
                ttip_prec = ''
                logging.warning(f'Actual day forcast n° {d} : No precipitation info provided')
            else:
                ttip_prec = ttip_prec.replace('snow', self.tr('snow'))
                ttip_prec = ttip_prec.replace('rain', self.tr('rain'))
                ttip += ttip_prec
            if self.bft_bool is True:
                windspeed = self.convertToBeaufort(windspeed)
            if self.wind_km_bool:
                windspeed = '{0:.1f}'.format(float(windspeed) * 3.6)
            ttip += f'{str(windspeed)} {self.unit_system_wind}'
            if winddircode != '':
                wind = f'{self.wind_direction[winddircode]} '
            else:
                logging.warning(
                    'Wind direction code is missing: {}'.format(
                        str(winddircode)
                    )
                )
            if clouds != '':
                try:
                    # In JSON there is no clouds description
                    clouds_translated = (
                        self.conditions[self.clouds_name_dic[clouds.lower()]]
                    )
                except KeyError:
                    logging.warning(
                        'The clouding description in json is not relevant'
                    )
                    clouds_translated = ''
            else:
                logging.warning(f'Clouding name is missing: {str(clouds)}')
            clouds_cond = f'{clouds_translated} {str(cloudspercent)}%'
            ttip += f'{wind}<br/>{clouds_cond}<br/>'
            pressure_local = QCoreApplication.translate(
                'Tootltip forcast of the day',
                'Pressure',
                'Weather info window'
            )
            humidity_local = QCoreApplication.translate(
                'Tootltip forcast of the day',
                'Humidity',
                'Weather info window'
            )
            ttip += f'{pressure_local} {pressure}  hPa<br/>'
            ttip += f'{humidity_local} {humidity} %'
            daytime.setToolTip(ttip)
            self.dayforecast_temp_layout.addWidget(daytime)

    def ozone_fetch(self):
        logging.debug('Download ozone info...')
        if hasattr(self, 'ozone_thread'):
            if self.ozone_thread.isRunning():
                logging.debug('Terminate running ozone thread...')
                self.ozone_thread.terminate()
                self.ozone_thread.wait()
        self.ozone_thread = Ozone(self.uv_coord)
        self.ozone_thread.o3_signal['PyQt_PyObject'].connect(self.ozone_index)
        self.ozone_thread.start()

    def ozone_index(self, index):
        logging.debug(f'Ozone index : {index}')
        try:
            du = int(index)
            o3_color = self.ozone_du(du)
            factor = f'{str(du)[:1]}.{str(du)[1:2]}'
            gauge = '◼' * round(float(factor))
            logging.debug(f'Ozone gauge: {gauge}')
        except:
            du = '-'
            o3_color = None
        du_unit = QCoreApplication.translate(
            'Dobson Units',
            'DU',
            'Ozone value label'
        )
        if o3_color is not None:
            self.ozone_value_label.setText(
                '<font color=>{0} {1}</font><font color={2}> {3}</font>'.format(
                    str(du),
                    du_unit,
                    o3_color,
                    gauge
                )
            )
            self.ozone_value_label.setToolTip(
                QCoreApplication.translate(
                    'Ozone value tooltip',
                    '''The average amount of ozone in the <br/> atmosphere is
                    roughly 300 Dobson Units. What scientists call the
                    Antarctic Ozone “Hole” is an area where the ozone
                    concentration drops to an average of about 100 Dobson
                    Units.''',
                    'http://ozonewatch.gsfc.nasa.gov/facts/dobson_SH.html'
                )
            )
        else:
            self.ozone_value_label.setText(
                f'<font color=>{str(du)}</font>'
            )
        if du != '-':
            self.over_grid.addWidget(self.ozone_label, 9, 0)
            self.over_grid.addWidget(self.ozone_value_label, 9, 1)

    def uv_fetch(self):
        logging.debug('Download uv info...')
        self.uv_thread = Uv(self.uv_coord)
        self.uv_thread.uv_signal['PyQt_PyObject'].connect(self.uv_index)
        self.uv_thread.start()

    def uv_index(self, index):
        uv_gauge = '-'
        uv_color = self.uv_color(index)
        if uv_color[1] != 'None':
            uv_gauge = '◼' * int(round(float(index)))
            if uv_gauge == '':
                uv_gauge = '◼'
            self.uv_value_label.setText(
                '<font color=>{0} {1}</font><br/><font color={2}><b>{3}</b></font>'.format(
                    '{0:.1f}'.format(float(index)),
                    self.uv_risk[uv_color[1]],
                    uv_color[0],
                    uv_gauge
                )
            )
        else:
            self.uv_value_label.setText(f'<font color=>{uv_gauge}</font>')
        logging.debug(f'UV gauge ◼: {uv_gauge}')
        self.uv_value_label.setToolTip(self.uv_recommend[uv_color[1]])
        if uv_gauge == '-':
            self.uv_label.hide()
            self.uv_value_label.hide()
        else:
            self.uv_label.show()
            self.uv_value_label.show()

    def dayiconfetch(self):
        '''Icons for the forecast of the day'''
        self.clearLayout(self.dayforecast_layout)
        logging.debug('Download forecast icons for the day...')
        self.system_icons = self.settings.value('SystemIcons') or 'False'
        if self.system_icons == 'True':
            for icon in self.dayforecast_icon_list:
                logging.debug(
                    f'Day forecast icons\n'
                    f'Use the system icon "{self.system_icons_dico.get(icon, False)}" '
                    f'for the openweathermap icon "{icon}"'
                )
                image = QIcon.fromTheme(self.system_icons_dico[icon])
                if image.name() == '':
                    logging.critical(
                        f"The icon {self.system_icons_dico.get(icon, False)} "
                        f"doesn't exist in the system icons theme {QIcon.themeName()}"
                    )

                iconlabel = QLabel()
                iconlabel.setAlignment(Qt.AlignHCenter)
                iconpixmap = image.pixmap(QSize(50, 50))
                iconlabel.setPixmap(iconpixmap)
                shadow = self.shadow_effect()
                iconlabel.setGraphicsEffect(shadow)
                try:
                    iconlabel.setToolTip(self.dayforecast_weather_list.pop(0))
                    self.dayforecast_layout.addWidget(iconlabel)
                except IndexError as error:
                    logging.error(f'{str(error)} dayforecast_weather_list')
        else:
            self.day_download_thread = IconDownload(
                self.forecast_icon_url, self.dayforecast_icon_list
            )
            self.day_download_thread.wimage['PyQt_PyObject'].connect(self.dayiconwidget)
            self.day_download_thread.url_error_signal['QString'].connect(self.errorIconFetch)
            self.day_download_thread.start()

    def dayiconwidget(self, icon):
        '''Forecast icons of the day'''
        image = QImage()
        image.loadFromData(icon)
        iconlabel = QLabel()
        iconlabel.setAlignment(Qt.AlignHCenter)
        iconpixmap = QPixmap(image)
        iconlabel.setPixmap(iconpixmap)
        try:
            iconlabel.setToolTip(self.dayforecast_weather_list.pop(0))
            self.dayforecast_layout.addWidget(iconlabel)
        except IndexError as error:
            logging.error(f'{str(error)} dayforecast_weather_list')

    def moveEvent(self, event):
        self.settings.setValue("MainWindow/Geometry", self.saveGeometry())

    def resizeEvent(self, event):
        self.settings.setValue("MainWindow/Geometry", self.saveGeometry())

    def hideEvent(self, event):
        self.settings.setValue("MainWindow/Geometry", self.saveGeometry())

    def errorIconFetch(self, error):
        logging.error(f'error in download of forecast icon:\n{error}')

    def icon_loading(self):
        self.gif_loading = QMovie(":/loading")
        self.gif_loading.frameChanged.connect(self.update_gif)
        self.gif_loading.start()

    def update_gif(self):
        gif_frame = self.gif_loading.currentPixmap()
        self.systray.setIcon(QIcon(gif_frame))

    def icon_city_loading(self):
        self.city_label.setText('▉')
        self.cityChangeTimer.start(20)

    def update_city_gif(self):
        current = self.city_label.text()
        current += '▌'
        if len(current) > 35:
            current = '▉'
        self.city_label.setText(current)

    def manual_refresh(self):
        self.tentatives = 0
        self.refresh()

    def wheelEvent(self, event):
        if hasattr(self, 'day_download_thread'):
            if self.day_download_thread.isRunning():
                logging.debug(
                    'WheelEvent: Downloading icons - remaining thread "day_download_thread"...'
                )
                return
        if hasattr(self, 'download_thread'):
            if self.download_thread.isRunning():
                logging.debug(
                    'WheelEvent: Downloading icons - remaining thread "download_thread"...'
                )
                return

        self.icon_city_loading()
        cities = eval(self.settings.value('CityList') or [])
        if len(cities) == 0:
            return
        cities_trans = self.settings.value('CitiesTranslation') or '{}'
        cities_trans_dict = eval(cities_trans)
        direction = event.angleDelta().y()
        actual_city = self.current_city_display
        for key, value in cities_trans_dict.items():
            if self.current_city_display == key:
                actual_city = key
        if actual_city not in cities:
            cities.append(actual_city)
        current_city_index = cities.index(actual_city)

        if direction > 0:
            current_city_index += 1
            if current_city_index >= len(cities):
                current_city_index = 0
        else:
            current_city_index -= 1
            if current_city_index < 0:
                current_city_index = len(cities) - 1
        self.current_city_display = cities[current_city_index]
        self.city, self.country, self.id_ = self.current_city_display.split('_')
        self.timer.singleShot(500, self.refresh)

    def cities_menu(self):
        self.citiesMenu.clear()
        cities = self.settings.value('CityList') or []
        cities_trans = self.settings.value('CitiesTranslation') or '{}'
        cities_trans_dict = eval(cities_trans)
        if type(cities) is str:
            cities = eval(cities)

        # If we delete all cities it results to a '__'
        if (
            cities is not None
            and cities != ''
            and cities != '[]'
            and cities != ['__']
        ):
            if type(cities) is not list:
                # FIXME sometimes the list of cities is read as a string (?)
                # eval to a list
                cities = eval(cities)
            # Create the cities list menu
            for city in cities:
                if city in cities_trans_dict:
                    city = cities_trans_dict[city]
                action = QAction(city, self)
                action.triggered.connect(partial(self.changecity, city))
                self.citiesMenu.addAction(action)
        else:
            self.empty_cities_list()

    @pyqtSlot(str)
    def changecity(self, city):
        if hasattr(self, 'city_label'):
            self.icon_city_loading()
        cities_list = self.settings.value('CityList')
        cities_trans = self.settings.value('CitiesTranslation') or '{}'
        self.cities_trans_dict = eval(cities_trans)
        logging.debug(f'Cities {str(cities_list)}')
        if cities_list is None:
            self.empty_cities_list()
        if type(cities_list) is not list:
            # FIXME some times is read as string (?)
            cities_list = eval(cities_list)
        for town in cities_list:
            if town == self.find_city_key(city):
                ind = cities_list.index(town)
                self.current_city_display = cities_list[ind]
        self.refresh()

    def find_city_key(self, city):
        for key, value in self.cities_trans_dict.items():
            if value == city:
                return key
        return city

    def empty_cities_list(self):
        self.citiesMenu.addAction(self.tr('Empty list'))

    def refresh(self):
        if (
            hasattr(self, 'overviewcitydlg')
            and not self.cityChangeTimer.isActive()
        ):
            self.icon_city_loading()
        self.inerror = False
        self.systray.setIcon(QIcon(':/noicon'))
        self.systray.setToolTip(self.tr('Fetching weather data...'))
        if self.id_ == '':
            # Clear the menu, no cities configured
            self.citiesMenu.clear()
            self.empty_cities_list()
            self.timer.singleShot(2000, self.firsttime)
            self.id_ = ''
            self.systray.setToolTip(self.tr('No city configured'))
            return
        self.city, self.country, self.id_ = self.current_city_display.split('_')
        self.unit = self.settings.value('Unit') or 'metric'
        self.wind_unit_speed = self.settings.value('Wind_unit') or 'df'
        self.suffix = f'&mode=xml&units={self.unit}{self.appid}'
        self.interval = int(self.settings.value('Interval') or 30) * 60 * 1000
        self.timer.start(self.interval)
        self.update()

    def firsttime(self):
        self.temp = ''
        self.wIcon = QPixmap(':/noicon')
        self.systray.showMessage(
            'meteo-qt:\n',
            '{0}\n{1}'.format(
                self.tr('No city has been configured yet.'),
                self.tr('Right click on the icon and click on Settings.')
            )
        )

    def update(self):
        if hasattr(self, 'downloadThread'):
            if self.downloadThread.isRunning():
                logging.debug('remaining thread...')
                return
        logging.debug('Update...')
        self.icon_loading()
        self.wIcon = QPixmap(':/noicon')
        self.downloadThread = Download(
            self.wIconUrl, self.baseurl, self.day_forecast_url,
            self.forecast6_url, self.id_, self.suffix
        )
        self.downloadThread.wimage['PyQt_PyObject'].connect(self.makeicon)
        self.downloadThread.weather_icon_signal.connect(self.weather_icon_name_set)
        self.downloadThread.finished.connect(self.tray)
        self.downloadThread.xmlpage['PyQt_PyObject'].connect(self.weatherdata)
        self.downloadThread.day_forecast_rawpage.connect(self.dayforecast)
        self.forcast6daysBool = False
        self.downloadThread.forecast6_rawpage.connect(self.forecast6)
        self.downloadThread.uv_signal.connect(self.uv)
        self.downloadThread.error.connect(self.error)
        self.downloadThread.done.connect(self.done)
        self.downloadThread.start()

    def uv(self, value):
        self.uv_coord = value

    def forecast6(self, data):
        self.forcast6daysBool = True
        self.forecast6_data = data

    def dayforecast(self, data):
        if type(data) == dict:
            self.json_data_bool = True
        else:
            self.json_data_bool = False
        self.dayforecast_data = data

    def done(self, done):
        self.cityChangeTimer.stop()
        if done == 0:
            self.inerror = False
        elif done == 1:
            self.inerror = True
            logging.debug('Trying to retrieve data...')
            self.timer.singleShot(10000, self.try_again)
            return
        if hasattr(self, 'dayforecast_data'):
            self.overviewcity()
            return
        else:
            self.try_again()

    def try_again(self):
        self.nodata_message()
        logging.debug(f'Attempts: {str(self.tentatives)}')
        self.tentatives += 1
        self.timer.singleShot(5000, self.refresh)

    def nodata_message(self):
        nodata = QCoreApplication.translate(
            "Tray icon",
            "Searching for weather data...",
            "Tooltip (when mouse over the icon"
        )
        self.systray.setToolTip(nodata)
        self.notification = nodata

    def error(self, error):
        logging.error(f'Error:\n{str(error)}')
        self.nodata_message()
        self.timer.start(self.interval)
        self.inerror = True

    def weather_icon_name_set(self, iconname):
        self.weather_icon_name = iconname

    def makeicon(self, data):
        def make_icon(data):
            image = QImage()
            image.loadFromData(data)
            self.wIcon = QPixmap(image)
        self.system_icons = self.settings.value('SystemIcons') or 'False'
        if self.system_icons == 'True':
            logging.debug(
                f'Use the system icon "{self.system_icons_dico.get(self.weather_icon_name, False)}" '
                f'for the openweathermap icon "{self.weather_icon_name}"'
            )
            image = QIcon.fromTheme(self.system_icons_dico[self.weather_icon_name])
            if image.name() == '':
                logging.critical(
                    f"The icon {self.system_icons_dico.get(self.weather_icon_name, False)} "
                    f"doesn't exist in the system icons theme {QIcon.themeName()}"
                )
                make_icon(data)
            else:
                self.wIcon = image.pixmap(QSize(50, 50))
        else:
            make_icon(data)

    def weatherdata(self, tree):
        if self.inerror:
            return

        for element in tree.iter():

            if element.tag == 'sun':
                self.weatherDataDico['Sunrise'] = element.get('rise')
                self.weatherDataDico['Sunset'] = element.get('set')

            if element.tag == 'temperature':
                self.tempFloat = element.get('value')
                self.temp = f' {str(round(float(self.tempFloat)))}°'
                self.temp_decimal = (
                    '{}°'.format(
                        '{0:.1f}'.format(float(self.tempFloat))
                    )
                )

            if element.tag == 'weather':
                self.meteo = element.get('value')
                meteo_condition = element.get('number')
                try:
                    self.meteo = self.conditions[meteo_condition]
                except KeyError:
                    logging.debug(
                        'Cannot find localisation string for'
                        ' meteo_condition:'
                        f'{str(meteo_condition)}'
                    )
                    pass

            if element.tag == 'clouds':
                clouds = element.get('name')
                clouds_percent = element.get('value') + '%'
                try:
                    clouds = self.clouds[clouds]
                    clouds = self.conditions[clouds]
                except KeyError:
                    logging.debug(
                        f'Cannot find localisation string for clouds: {str(clouds)}'
                    )
                    pass

            if element.tag == 'speed':
                wind_value = element.get('value')
                wind = element.get('name').lower()
                try:
                    wind = self.wind[wind]
                    wind = self.conditions[wind]
                except KeyError:
                    logging.debug(
                        f'Cannot find localisation string for wind:{str(wind)}'
                    )
                    pass
            if element.tag == 'direction':
                wind_codes_english = element.get('code')
                wind_dir_value = element.get('value')
                wind_dir = element.get('name')

                try:
                    wind_dir_value = str(int(float(wind_dir_value)))
                except TypeError:
                    wind_dir_value = ''

                try:
                    wind_codes = self.wind_codes[wind_codes_english]
                except (KeyError, UnboundLocalError):
                    logging.debug(
                        f'Cannot find localisation string for wind_codes: {str(wind_codes_english)}'
                    )
                    wind_codes = wind_codes_english

                if wind_codes is None:
                    wind_codes = ''

                try:
                    wind_dir = self.wind_dir[wind_codes_english]
                except KeyError:
                    logging.debug(
                        f'Cannot find localisation string for wind_dir: {str(wind_dir)}'
                    )
                    if wind_dir is None:
                        wind_dir = ''

            if element.tag == 'humidity':
                self.weatherDataDico['Humidity'] = (
                    element.get('value'), element.get('unit')
                )

            if element.tag == 'pressure':
                self.weatherDataDico['Pressure'] = (
                    element.get('value'), element.get('unit')
                )

            if element.tag == 'precipitation':
                rain_mode = element.get('mode')
                rain_value = element.get('value')
                if rain_value is None:
                    rain_value = ''
                self.weatherDataDico['Precipitation'] = (
                    rain_mode, rain_value
                )

            if element.tag == 'feels_like':
                t_unit = {'celsius': '°C', 'fahrenheit': '°F'}
                self.weatherDataDico['Feels_like'] = [element.get('value'), t_unit[element.get('unit')]]

        self.city_weather_info = (
            '{0} {1} {2} {3}'.format(
                self.city,
                self.country,
                self.temp_decimal,
                self.meteo
            )
        )
        self.tooltip_weather()
        self.notification = self.city_weather_info
        self.weatherDataDico['Id'] = self.id_
        self.weatherDataDico['City'] = self.city
        self.weatherDataDico['Country'] = self.country
        self.weatherDataDico['Temp'] = f'{self.tempFloat}°'
        self.weatherDataDico['Meteo'] = self.meteo

        self.weatherDataDico['Wind'] = (
            wind_value,
            wind,
            wind_dir_value,
            wind_codes,
            wind_dir
        )
        self.weatherDataDico['Clouds'] = (f'{clouds_percent} {clouds}')

        if self.id_ not in self.trendCities_dic:
            # dict {'id': 'hPa', , '',  'T°', 'temp_trend', 'weather changedBool'}
            self.trendCities_dic[self.id_] = [''] * 5
        # hPa trend
        pressure = float(self.weatherDataDico['Pressure'][0])
        if (
            self.id_ in self.trendCities_dic
            and self.trendCities_dic[self.id_][0] != ''
        ):
            self.hPaTrend = pressure - float(self.trendCities_dic[self.id_][0])
        else:
            self.hPaTrend = 0
        self.trendCities_dic[self.id_][0] = pressure
        # Temperature trend
        self.notifier()

    def notifier(self):
        ''' The notification is being shown:
        On a city change or first launch or if the temperature changes
        The notification is not shown if is turned off from the settings.
        The tray tooltip is set here '''
        temp = float(self.tempFloat)
        if (
            self.id_ in self.trendCities_dic
            and self.trendCities_dic[self.id_][2] != ''
        ):
            if temp > float(self.trendCities_dic[self.id_][2]):
                self.temp_trend = " ↗"
                self.trendCities_dic[self.id_][3] = self.temp_trend
            elif temp < float(self.trendCities_dic[self.id_][2]):
                self.temp_trend = " ↘"
                self.trendCities_dic[self.id_][3] = self.temp_trend
            else:
                self.temp_trend = self.trendCities_dic[self.id_][3]
            if temp == self.trendCities_dic[self.id_][2]:
                self.trendCities_dic[self.id_][4] = False
            else:
                self.trendCities_dic[self.id_][4] = True

        self.trendCities_dic[self.id_][2] = temp
        self.systray.setToolTip(self.city_weather_info + self.temp_trend)

    def tooltip_weather(self):
        # Creation of the tray tootltip
        trans_cities = self.settings.value('CitiesTranslation') or '{}'
        trans_cities_dict = eval(trans_cities)
        city = f'{self.city}_{self.country}_{self.id_}'
        feels_like = (
            '{0} {1}'.format(
                self.feels_like_translated,
                ' '.join(fl for fl in self.weatherDataDico['Feels_like'])
            )
        )

        if city in trans_cities_dict:
            self.city_weather_info = (
                '{0} {1}<br/>{2}<br/>{3}'.format(
                    trans_cities_dict[city],
                    self.temp_decimal,
                    feels_like,
                    self.meteo
                )
            )
        else:
            self.city_weather_info = (
                '{0} {1} {2}<br/>{3}<br/>{4}'.format(
                    self.city,
                    self.country,
                    self.temp_decimal,
                    feels_like,
                    self.meteo
                )
            )

    def tray(self):
        temp_decimal = eval(self.settings.value('Decimal') or 'False')
        try:
            if temp_decimal:
                temp_tray = self.temp_decimal
            else:
                temp_tray = self.temp
        except:
            # First time launch
            return
        if self.inerror or not hasattr(self, 'temp'):
            logging.critical('Cannot paint icon!')
            return
        try:
            self.gif_loading.stop()
        except:
            # In first time run the gif is not animated
            pass
        logging.debug('Paint tray icon...')
        # Place empty.png here to initialize the icon
        # don't paint the T° over the old value
        icon = QPixmap(':/empty')
        self.traycolor = self.settings.value('TrayColor') or ''
        self.fontsize = self.settings.value('FontSize') or '18'
        self.tray_type = self.settings.value('TrayType') or 'icon&temp'
        if self.tray_type == 'feels_like_temp' or self.tray_type == 'icon&feels_like':
            temp_tray = '{0:.0f}'.format(float(self.weatherDataDico['Feels_like'][0]))
            if temp_decimal:
                temp_tray = '{0:.1f}'.format(float(self.weatherDataDico['Feels_like'][0]))
            temp_tray += '°'
        pt = QPainter()
        pt.begin(icon)
        if self.tray_type != 'temp' and self.tray_type != 'feels_like_temp':
            pt.drawPixmap(0, -12, 64, 64, self.wIcon)
        self.bold_set = self.settings.value('Bold') or 'False'
        if self.bold_set == 'True':
            br = QFont.Bold
        else:
            br = QFont.Normal
        pt.setFont(QFont('sans-sertif', int(self.fontsize), weight=br))
        pt.setPen(QColor(self.traycolor))
        if self.tray_type == 'icon&temp' or self.tray_type == 'icon&feels_like':
            pt.drawText(
                icon.rect(),
                Qt.AlignBottom | Qt.AlignCenter,
                str(temp_tray)
            )
        if self.tray_type == 'temp' or self.tray_type == 'feels_like_temp':
            pt.drawText(icon.rect(), Qt.AlignCenter, str(temp_tray))
        pt.end()
        if self.tray_type == 'icon':
            self.systray.setIcon(QIcon(self.wIcon))
        else:
            self.systray.setIcon(QIcon(icon))
        if self.notifier_settings():
            try:
                if (
                    self.temp_trend != ''
                    or self.trendCities_dic[self.id_][1] == ''
                    or self.id_ != self.notifier_id
                ):
                    if not self.isVisible():
                        # Don't show the notification when window is open
                        # Show only if the temperature has changed
                        if (
                            self.trendCities_dic[self.id_][4] is
                                True or self.trendCities_dic[self.id_][4] == ''
                        ):
                            self.systray.showMessage(
                                'meteo-qt', f'{self.notification}{self.temp_trend}'
                            )
                            return
            except KeyError:
                return
        self.notifier_id = self.id_  # To always notify when city changes
        if self.temporary_city_status:
            self.restore_city()
        self.tentatives = 0
        self.tooltip_weather()
        logging.info(f'Actual weather status for: {self.notification}')

    def notifier_settings(self):
        notifier = self.settings.value('Notifications') or 'True'
        notifier = eval(notifier)
        if notifier:
            return True
        else:
            return False

    def restore_city(self):
        self.city = self.settings.value('City') or ''
        self.country = self.settings.value('Country') or ''
        self.id_ = self.settings.value('ID') or ''
        self.current_city_display = f'{self.city}_{self.country}_{self.id_}'
        self.temporary_city_status = False

    def showpanel(self):
        self.activate(3)

    def activate(self, reason):
        # Option to start with the panel closed, true by defaut
        # starting with the panel open can be useful for users who don't have plasma
        # installed (to set keyboard shortcuts or other default window behaviours)
        start_minimized = self.settings.value('StartMinimized') or 'True'
        if reason == 3:
            if self.inerror or self.id_ is None or self.id_ == '':
                return
            if self.isVisible() and start_minimized == 'True':
                self.hide()
            else:
                self.show()
        elif reason == 1:
            self.menu.popup(QCursor.pos())

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.settings.setValue("MainWindow/State", self.saveState())

    def overview(self):
        if self.inerror or len(self.weatherDataDico) == 0:
            return
        self.show()

    def config_save(self):
        logging.debug('Config saving...')
        city = self.settings.value('City')
        id_ = self.settings.value('ID')
        country = self.settings.value('Country')
        unit = self.settings.value('Unit')
        wind_unit_speed = self.settings.value('Wind_unit')
        traycolor = self.settings.value('TrayColor')
        tray_type = self.settings.value('TrayType')
        system_icons = self.settings.value('SystemIcons')
        fontsize = self.settings.value('FontSize')
        bold_set = self.settings.value('Bold')
        language = self.settings.value('Language')
        decimal = self.settings.value('Decimal')
        self.appid = f'&APPID={self.settings.value("APPID")}' or ''
        if language != self.language and language is not None:
            self.systray.showMessage(
                'meteo-qt:',
                QCoreApplication.translate(
                    "System tray notification",
                    "The application has to be restarted to apply the language setting",
                    ''
                )
            )
            self.language = language
        # Check if update is needed
        if traycolor is None:
            traycolor = ''
        if (
            self.traycolor != traycolor
            or self.tray_type != tray_type
            or self.fontsize != fontsize or self.bold_set != bold_set
            or decimal != self.temp_decimal
        ):
            self.tray()
        if (
            city == self.city
            and id_ == self.id_
            and country == self.country
            and unit == self.unit
            and wind_unit_speed == self.wind_unit_speed
            and system_icons == self.system_icons
        ):
            return
        else:
            logging.debug('Apply changes from settings...')
            self.city, self.country, self.id_ = city, country, id_
            self.current_city_display = f'{city}_{country}_{id_}'
            self.refresh()

    def config(self):
        dialog = settings.MeteoSettings(self.accurate_url, self.appid, self)
        dialog.applied_signal.connect(self.config_save)
        if dialog.exec_() == 1:
            self.config_save()
            logging.debug('Update Cities menu...')
            self.cities_menu()

    def tempcity(self):
        dialog = searchcity.SearchCity(self.accurate_url, self.appid, self)
        dialog.id_signal[tuple].connect(self.citydata)
        dialog.city_signal[tuple].connect(self.citydata)
        dialog.country_signal[tuple].connect(self.citydata)
        if dialog.exec_():
            self.temporary_city_status = True
            self.current_city_display = f'{self.city}_{self.country}_{self.id_}'
            self.systray.setToolTip(self.tr('Fetching weather data...'))
            self.refresh()

    def citydata(self, what):
        if what[0] == 'City':
            self.city = what[1]
        elif what[0] == 'Country':
            self.country = what[1]
        elif what[0] == 'ID':
            self.id_ = what[1]

    def about(self):
        title = self.tr(
            """<b>meteo-qt</b> v{0}
            <br/>License: GPLv3
            <br/>Python {1} - Qt {2} - PyQt {3} on {4}"""
        ).format(
            __version__, platform.python_version(),
            QT_VERSION_STR, PYQT_VERSION_STR, platform.system()
        )
        image = ':/logo'
        text = self.tr(
            """<p>Author: Dimitrios Glentadakis
            <a href="mailto:dglent@free.fr">dglent@free.fr</a>
            <p>A simple application showing the weather status
            information on the system tray.
            <p>Website: <a href="https://github.com/dglent/meteo-qt">
            https://github.com/dglent/meteo-qt</a>
            <br/>Data source: <a href="http://openweathermap.org/">
            OpenWeatherMap</a>.
            <br/>This software uses icons from the
            <a href="http://www.kde.org/">Oxygen Project</a>.
            <p>To translate meteo-qt in your language or contribute to
            current translations, you can use the
            <a href="https://www.transifex.com/projects/p/meteo-qt/">
            Transifex</a> platform.
            <p>If you want to report a dysfunction or a suggestion,
            feel free to open an issue in
            <a href="https://github.com/dglent/meteo-qt/issues">
            github</a>."""
        )

        dialog = about_dlg.AboutDialog(title, text, image, self)
        dialog.exec_()


class Download(QThread):
    wimage = pyqtSignal(['PyQt_PyObject'])
    weather_icon_signal = pyqtSignal(['QString'])
    xmlpage = pyqtSignal(['PyQt_PyObject'])
    forecast6_rawpage = pyqtSignal(['PyQt_PyObject'])
    day_forecast_rawpage = pyqtSignal(['PyQt_PyObject'])
    uv_signal = pyqtSignal(['PyQt_PyObject'])
    error = pyqtSignal(['QString'])
    done = pyqtSignal([int])

    def __init__(self, iconurl, baseurl, day_forecast_url, forecast6_url, id_,
                 suffix, parent=None):
        QThread.__init__(self, parent)
        self.wIconUrl = iconurl
        self.baseurl = baseurl
        self.day_forecast_url = day_forecast_url
        self.forecast6_url = forecast6_url
        self.id_ = id_
        self.suffix = suffix
        self.tentatives = 0
        self.settings = QSettings()

    def run(self):
        use_json_day_forecast = False
        use_proxy = self.settings.value('Proxy') or 'False'
        use_proxy = eval(use_proxy)
        proxy_auth = (
            self.settings.value('Use_proxy_authentification')
            or 'False'
        )
        proxy_auth = eval(proxy_auth)
        if use_proxy:
            proxy_url = self.settings.value('Proxy_url')
            proxy_port = self.settings.value('Proxy_port')
            proxy_tot = f'http://:{proxy_port}'
            if proxy_auth:
                proxy_user = self.settings.value('Proxy_user')
                proxy_password = self.settings.value('Proxy_pass')
                proxy_tot = (
                    f'http://{proxy_user}:{proxy_password}@{proxy_url}:{proxy_port}'
                )
            proxy = urllib.request.ProxyHandler(
                {"http": proxy_tot}
            )
            auth = urllib.request.HTTPBasicAuthHandler()
            opener = urllib.request.build_opener(
                proxy, auth, urllib.request.HTTPHandler
            )
            urllib.request.install_opener(opener)
        else:
            proxy_handler = urllib.request.ProxyHandler({})
            opener = urllib.request.build_opener(proxy_handler)
            urllib.request.install_opener(opener)
        done = 0

        logging.debug(
            f'Fetching url for 6 days: {self.forecast6_url}{self.id_}{self.suffix}&cnt=7'
        )
        reqforecast6 = (
            f'{self.forecast6_url}{self.id_}{self.suffix}&cnt=7'
        )
        try:
            reqforecast6 = urllib.request.urlopen(
                f'{self.forecast6_url}{self.id_}{self.suffix}&cnt=7',
                timeout=5
            )
            pageforecast6 = reqforecast6.read()
            if str(pageforecast6).count('ClientError') > 0:
                raise TypeError
            treeforecast6 = etree.fromstring(pageforecast6)
            forcast6days = True
        except (
                timeout,
                urllib.error.HTTPError,
                urllib.error.URLError,
                etree.XMLSyntaxError,
                TypeError
        ) as e:
            forcast6days = False
            logging.error(f'Url of 6 days forcast not available: {str(reqforecast6)}')
            logging.error(f'6 days forcast not available: {str(e)}')

        try:
            logging.debug(
                f'Fetching url for actual weather: {self.baseurl}{self.id_}{self.suffix}'
            )
            req = urllib.request.urlopen(
                f'{self.baseurl}{self.id_}{self.suffix}',
                timeout=5
            )
            logging.debug(
                'Fetching url for forecast of the day + 4: {0}{1}{2}'.format(
                    self.day_forecast_url,
                    self.id_,
                    self.suffix
                )
            )
            reqdayforecast = urllib.request.urlopen(
                f'{self.day_forecast_url}{self.id_}{self.suffix}',
                timeout=5
            )
            page = req.read()
            pagedayforecast = reqdayforecast.read()
            if self.html404(page, 'city'):
                raise urllib.error.HTTPError
            elif self.html404(pagedayforecast, 'day_forecast'):
                # Try with json
                logging.debug(
                    'Fetching json url for forecast of the day: '
                    f'{self.day_forecast_url}{self.id_}'
                    f'{self.suffix.replace("xml", "json")}'
                )
                reqdayforecast = urllib.request.urlopen(
                    f'{self.day_forecast_url}{self.id_}{self.suffix.replace("xml", "json")}',
                    timeout=5
                )
                pagedayforecast = reqdayforecast.read().decode('utf-8')
                if self.html404(pagedayforecast, 'day_forecast'):
                    raise urllib.error.HTTPError
                else:
                    treedayforecast = json.loads(pagedayforecast)
                    use_json_day_forecast = True
                    logging.debug(
                        'Found json page for the forecast of the day'
                    )
            try:
                tree = etree.fromstring(page)
                lat = tree[0][0].get('lat')
                lon = tree[0][0].get('lon')
                weather_icon = tree[9].get('icon')
                for var_ in [lat, lon, weather_icon]:
                    if isinstance(var_, type(None)):
                        raise TypeError
            except TypeError:
                logging.debug(
                    f'Error, use JSON page for the actual weather info {str(traceback.print_exc())}'
                )
                req = urllib.request.urlopen(
                    f'{self.baseurl}{self.id_}{self.suffix.replace("xml", "json")}',
                    timeout=5
                )
                page = req.read().decode('utf-8').replace("'", '"')
                actual_weather_dic = json.loads(page)
                lat = str(actual_weather_dic["coord"]["lat"])
                lon = str(actual_weather_dic["coord"]["lon"])
                weather_icon = actual_weather_dic["weather"][0]["icon"]

            uv_ind = (lat, lon)
            url = f'{self.wIconUrl}{weather_icon}.png'
            self.weather_icon_signal.emit(weather_icon)
            self.uv_signal['PyQt_PyObject'].emit(uv_ind)
            if not use_json_day_forecast:
                treedayforecast = etree.fromstring(pagedayforecast)

            logging.debug(f'Icon url: {url}')
            data = urllib.request.urlopen(url).read()
            if self.html404(data, 'icon'):
                raise urllib.error.HTTPError
            self.xmlpage['PyQt_PyObject'].emit(tree)
            self.wimage['PyQt_PyObject'].emit(data)
            if forcast6days:
                self.forecast6_rawpage['PyQt_PyObject'].emit(treeforecast6)
            self.day_forecast_rawpage['PyQt_PyObject'].emit(treedayforecast)
            self.done.emit(int(done))
        except (
                ConnectionResetError,
                urllib.error.HTTPError,
                urllib.error.URLError
        ) as error:
            if self.tentatives >= 10:
                done = 1
                try:
                    m_error = (
                        f'{self.tr("Error:")}\n{str(error.code)} {str(error.reason)}'
                    )
                except:
                    m_error = str(error)
                logging.error(m_error)
                self.error['QString'].emit(m_error)
                self.done.emit(int(done))
                return
            else:
                self.tentatives += 1
                logging.warning(f'Error: {str(error)}')
                logging.info(f'Try again...{str(self.tentatives)}')
                self.run()
        except timeout:
            if self.tentatives >= 10:
                done = 1
                logging.error('Timeout error, abandon...')
                self.done.emit(int(done))
                return
            else:
                self.tentatives += 1
                logging.warning(
                    f'5 secondes timeout, new tentative: {str(self.tentatives)}'
                )
                self.run()
        except (etree.XMLSyntaxError) as error:
            logging.critical(f'Error: {str(error)}')
            done = 1
            self.done.emit(int(done))

        logging.debug('Download thread done')

    def html404(self, page, what):
        try:
            dico = eval(page.decode('utf-8'))
            code = dico['cod']
            message = dico['message']
            self.error_message = f'{code} {message}@{what}'
            logging.debug(str(self.error_message))
            return True
        except:
            return False


class Ozone(QThread):
    o3_signal = pyqtSignal(['PyQt_PyObject'])

    def __init__(self, coord, parent=None):
        QThread.__init__(self, parent)
        self.coord = coord
        self.settings = QSettings()
        self.appid = self.settings.value('APPID') or ''

    def run(self):
        use_proxy = self.settings.value('Proxy') or 'False'
        use_proxy = eval(use_proxy)
        proxy_auth = (
            self.settings.value('Use_proxy_authentification') or 'False'
        )
        proxy_auth = eval(proxy_auth)
        if use_proxy:
            proxy_url = self.settings.value('Proxy_url')
            proxy_port = self.settings.value('Proxy_port')
            proxy_tot = f'http://:{proxy_port}'
            if proxy_auth:
                proxy_user = self.settings.value('Proxy_user')
                proxy_password = self.settings.value('Proxy_pass')
                proxy_tot = (
                    f'http://{proxy_user}:{proxy_password}@{proxy_url}:{proxy_port}'
                )
            proxy = urllib.request.ProxyHandler({"http": proxy_tot})
            auth = urllib.request.HTTPBasicAuthHandler()
            opener = urllib.request.build_opener(
                proxy, auth, urllib.request.HTTPHandler
            )
            urllib.request.install_opener(opener)
        else:
            proxy_handler = urllib.request.ProxyHandler({})
            opener = urllib.request.build_opener(proxy_handler)
            urllib.request.install_opener(opener)
        try:
            lat = self.coord[0]
            lon = self.coord[1]
            url = (
                'http://api.openweathermap.org/pollution/v1/o3/{0},{1}/current.json?appid={2}'.format(
                    lat,
                    lon,
                    self.appid
                )
            )
            logging.debug(f'Fetching url for ozone index: {str(url)}')
            req = urllib.request.urlopen(url, timeout=5)
            page = req.read()
            dico_value = eval(page)
            o3_ind = dico_value['data']
            logging.debug(f'Ozone index: {str(o3_ind)}')
        except:
            o3_ind = '-'
            logging.error('Cannot find Ozone index')
        self.o3_signal['PyQt_PyObject'].emit(o3_ind)


class Uv(QThread):
    uv_signal = pyqtSignal(['PyQt_PyObject'])

    def __init__(self, uv_coord, parent=None):
        QThread.__init__(self, parent)
        self.uv_coord = uv_coord
        self.settings = QSettings()
        self.appid = self.settings.value('APPID') or ''

    def run(self):
        use_proxy = self.settings.value('Proxy') or 'False'
        use_proxy = eval(use_proxy)
        proxy_auth = (
            self.settings.value('Use_proxy_authentification') or 'False'
        )
        proxy_auth = eval(proxy_auth)
        if use_proxy:
            proxy_url = self.settings.value('Proxy_url')
            proxy_port = self.settings.value('Proxy_port')
            proxy_tot = f'http://:{proxy_port}'
            if proxy_auth:
                proxy_user = self.settings.value('Proxy_user')
                proxy_password = self.settings.value('Proxy_pass')
                proxy_tot = (
                    f'http://{proxy_user}:{proxy_password}@{proxy_url}:{proxy_port}'
                )
            proxy = urllib.request.ProxyHandler({"http": proxy_tot})
            auth = urllib.request.HTTPBasicAuthHandler()
            opener = urllib.request.build_opener(
                proxy, auth, urllib.request.HTTPHandler
            )
            urllib.request.install_opener(opener)
        else:
            proxy_handler = urllib.request.ProxyHandler({})
            opener = urllib.request.build_opener(proxy_handler)
            urllib.request.install_opener(opener)
        try:
            lat = self.uv_coord[0]
            lon = self.uv_coord[1]
            url = (
                f'http://api.openweathermap.org/data/2.5/uvi?lat={lat}&lon={lon}&appid={self.appid}'
            )
            logging.debug(f'Fetching url for uv index: {str(url)}')
            req = urllib.request.urlopen(url, timeout=5)
            page = req.read().decode('utf-8')
            dicUV = json.loads(page)
            uv_ind = dicUV['value']
            logging.debug(f'UV index: {str(uv_ind)}')
        except:
            uv_ind = '-'
            logging.error('Cannot find UV index')
        self.uv_signal['PyQt_PyObject'].emit(uv_ind)


class IconDownload(QThread):
    url_error_signal = pyqtSignal(['QString'])
    wimage = pyqtSignal(['PyQt_PyObject'])

    def __init__(self, icon_url, icon, parent=None):
        QThread.__init__(self, parent)
        self.icon_url = icon_url
        self.icon = icon
        self.tentatives = 0
        # Some times server sends less data
        self.periods = 6
        periods = len(self.icon)
        if periods < 6:
            self.periods = periods
        self.settings = QSettings()

    def run(self):
        use_proxy = self.settings.value('Proxy') or 'False'
        use_proxy = eval(use_proxy)
        proxy_auth = (
            self.settings.value('Use_proxy_authentification') or 'False'
        )
        proxy_auth = eval(proxy_auth)
        if use_proxy:
            proxy_url = self.settings.value('Proxy_url')
            proxy_port = self.settings.value('Proxy_port')
            proxy_tot = 'http://' + ':' + proxy_port
            if proxy_auth:
                proxy_user = self.settings.value('Proxy_user')
                proxy_password = self.settings.value('Proxy_pass')
                proxy_tot = (
                    f'http://{proxy_user}:{proxy_password}@{proxy_url}:{proxy_port}'
                )
            proxy = urllib.request.ProxyHandler({"http": proxy_tot})
            auth = urllib.request.HTTPBasicAuthHandler()
            opener = urllib.request.build_opener(
                proxy, auth, urllib.request.HTTPHandler
            )
            urllib.request.install_opener(opener)
        else:
            proxy_handler = urllib.request.ProxyHandler({})
            opener = urllib.request.build_opener(proxy_handler)
            urllib.request.install_opener(opener)
        try:
            for i in range(self.periods):
                url = f'{self.icon_url}{self.icon[i]}.png'
                logging.debug(f'Icon downloading: {url}')
                data = urllib.request.urlopen(url, timeout=5).read()
                if self.html404(data, 'icon'):
                    self.url_error_signal['QString'].emit(self.error_message)
                    return
                self.wimage['PyQt_PyObject'].emit(data)
        except (urllib.error.HTTPError, urllib.error.URLError) as error:
            try:
                url_error = (
                    f'Error: {str(error.code)}: {str(error.reason)}'
                )
            except:
                url_error = error
            logging.error(str(url_error))
            self.url_error_signal['QString'].emit(url_error)
        except timeout:
            if self.tentatives >= 10:
                logging.error('Timeout error, abandon...')
                return
            else:
                self.tentatives += 1
                logging.info(
                    '5 secondes timeout, new tentative: '
                    + str(self.tentatives)
                )
                self.run()
        logging.debug('Download forecast icons thread done')

    def html404(self, page, what):
        try:
            dico = eval(page.decode('utf-8'))
            code = dico['cod']
            message = dico['message']
            self.error_message = f'{code} {message}@{what}'
            logging.error(self.error_message)
            return True
        except:
            return False


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setOrganizationName('meteo-qt')
    app.setOrganizationDomain('meteo-qt')
    app.setApplicationName('meteo-qt')
    app.setWindowIcon(QIcon(':/logo'))
    filePath = os.path.dirname(os.path.realpath(__file__))
    settings = QSettings()
    locale = settings.value('Language')
    if locale is None or locale == '':
        locale = QLocale.system().name()
    appTranslator = QTranslator()
    if os.path.exists(f'{filePath}/translations/'):
        appTranslator.load(
            filePath + f'/translations/meteo-qt_{locale}'
        )
    else:
        appTranslator.load(
            f'/usr/share/meteo_qt/translations/meteo-qt_{locale}'
        )
    app.installTranslator(appTranslator)
    qtTranslator = QTranslator()
    qtTranslator.load(
        f'qt_{locale}',
        QLibraryInfo.location(
            QLibraryInfo.TranslationsPath
        )
    )
    app.installTranslator(qtTranslator)

    logLevel = settings.value('Logging/Level')
    if logLevel == '' or logLevel is None:
        logLevel = 'INFO'
        settings.setValue('Logging/Level', 'INFO')

    logPath = os.path.dirname(settings.fileName())
    logFile = f'{logPath}/meteo-qt.log'
    if not os.path.exists(logPath):
        os.makedirs(logPath)
    if os.path.isfile(logFile):
        fsize = os.stat(logFile).st_size
        if fsize > 10240000:
            with open(logFile, 'rb') as rFile:
                rFile.seek(102400)
                logData = rFile.read()
            with open(logFile, 'wb') as wFile:
                wFile.write(logData)
            del logData

    logging.basicConfig(
        format='%(asctime)s %(levelname)s: %(message)s'
        '- %(lineno)s: %(module)s',
        datefmt='%Y/%m/%d %H:%M:%S',
        filename=logFile, level=logLevel
    )
    logger = logging.getLogger('meteo-qt')
    logger.setLevel(logLevel)
    loggerStream = logging.getLogger()
    handlerStream = logging.StreamHandler()
    loggerStreamFormatter = logging.Formatter(
        '%(levelname)s: %(message)s - %(lineno)s: %(module)s'
    )
    handlerStream.setFormatter(loggerStreamFormatter)
    loggerStream.addHandler(handlerStream)

    m = SystemTrayIcon()
    app.exec_()


def excepthook(exc_type, exc_value, tracebackobj):
    """
    Global function to catch unhandled exceptions.

    Parameters
    ----------
    exc_type : str
        exception type
    exc_value : int
        exception value
    tracebackobj : traceback
        traceback object
    """
    separator = '-' * 80

    now = f'{datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")} CRASH:'

    info = StringIO()
    traceback.print_tb(tracebackobj, None, info)
    info.seek(0)
    info = info.read()

    errmsg = '{}\t \n{}'.format(exc_type, exc_value)
    sections = [now, separator, errmsg, separator, info]
    msg = '\n'.join(sections)

    print(msg)

    settings = QSettings()
    logPath = os.path.dirname(settings.fileName())
    logFile = f'{logPath}/meteo-qt.log'
    with open(logFile, 'a') as logfile:
        logfile.write(msg)


sys.excepthook = excepthook

if __name__ == '__main__':
    main()
