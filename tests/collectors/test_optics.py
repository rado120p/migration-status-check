from lxml import etree

from migration_validator.collectors.optics import OpticsCollector

MX_S_LANES = etree.fromstring("""
<interface-information>
  <physical-interface>
    <name>et-0/0/5</name>
    <optics-diagnostics>
      <optics-diagnostics-lane-values>
        <lane-index>0</lane-index>
        <laser-rx-optical-power-dbm>-5.23</laser-rx-optical-power-dbm>
        <laser-output-power-dbm>-2.10</laser-output-power-dbm>
        <laser-rx-power-high-alarm>off</laser-rx-power-high-alarm>
        <laser-rx-power-low-alarm>on</laser-rx-power-low-alarm>
        <laser-rx-power-low-warn>off</laser-rx-power-low-warn>
      </optics-diagnostics-lane-values>
    </optics-diagnostics>
  </physical-interface>
</interface-information>
""")

MX_BEZ_LANES_KOHERENTNI = etree.fromstring("""
<interface-information>
  <physical-interface>
    <name>et-0/1/0</name>
    <optics-diagnostics>
      <rx-signal-avg-optical-power-dbm>-7.80</rx-signal-avg-optical-power-dbm>
      <laser-output-power-dbm>0.51</laser-output-power-dbm>
      <laser-rx-power-high-alarm>Off</laser-rx-power-high-alarm>
    </optics-diagnostics>
  </physical-interface>
</interface-information>
""")

MX_NEPRIPOJENY_PORT = etree.fromstring("""
<interface-information>
  <physical-interface>
    <name>et-0/0/9</name>
    <optics-diagnostics>
      <optics-diagnostics-lane-values>
        <lane-index>0</lane-index>
        <laser-rx-optical-power-dbm>-Inf</laser-rx-optical-power-dbm>
        <laser-output-power-dbm>-Inf</laser-output-power-dbm>
        <laser-rx-power-low-alarm>On</laser-rx-power-low-alarm>
      </optics-diagnostics-lane-values>
    </optics-diagnostics>
  </physical-interface>
</interface-information>
""")


def test_lane_varianta_a_zvednuty_alarm():
    result = OpticsCollector().parse(MX_S_LANES, "junos")
    lane = result["et-0/0/5"]["lanes"][0]
    assert lane["lane"] == 0
    assert lane["rx_power_dbm"] == -5.23
    assert lane["tx_power_dbm"] == -2.10
    assert lane["alarms"]["laser-rx-power-low-alarm"] is True
    assert lane["alarms"]["laser-rx-power-high-alarm"] is False
    assert lane["warnings"]["laser-rx-power-low-warn"] is False


def test_bez_lane_koherentni_rx_se_sleva_a_Off_je_off():
    result = OpticsCollector().parse(MX_BEZ_LANES_KOHERENTNI, "junos")
    lane = result["et-0/1/0"]["lanes"][0]
    assert lane["lane"] is None
    assert lane["rx_power_dbm"] == -7.80  # slouceno z rx-signal-avg
    assert lane["alarms"]["laser-rx-power-high-alarm"] is False  # "Off"


def test_port_bez_optiky_v_area_neni():
    xml = etree.fromstring(
        "<interface-information><physical-interface>"
        "<name>ge-0/0/0</name></physical-interface></interface-information>")
    assert OpticsCollector().parse(xml, "junos") == {}


def test_nepripojeny_port_ma_neconecny_vykon_ne_none():
    """-Inf na nepripojenem portu se musi zachovat, ne tise zmenit na None.

    Collector jen parsuje - interpretace (co s -Inf udelat na reportu) je
    az na checku v Tasku 12.
    """
    result = OpticsCollector().parse(MX_NEPRIPOJENY_PORT, "junos")
    lane = result["et-0/0/9"]["lanes"][0]
    assert lane["rx_power_dbm"] == float("-inf")
    assert lane["tx_power_dbm"] == float("-inf")
    assert lane["alarms"]["laser-rx-power-low-alarm"] is True


MX_MODULE_TEMPERATURE = etree.fromstring("""
<interface-information>
  <physical-interface>
    <name>et-0/0/11</name>
    <optics-diagnostics>
      <module-temperature>23 degrees C / 73 degrees F</module-temperature>
      <optics-diagnostics-lane-values>
        <lane-index>0</lane-index>
        <laser-rx-optical-power-dbm>-4.0</laser-rx-optical-power-dbm>
      </optics-diagnostics-lane-values>
    </optics-diagnostics>
  </physical-interface>
</interface-information>
""")


def test_temperature_se_bere_z_modulu_kdyz_lane_nema_vlastni():
    result = OpticsCollector().parse(MX_MODULE_TEMPERATURE, "junos")
    lane = result["et-0/0/11"]["lanes"][0]
    assert lane["temperature_c"] == 23.0


EVO_TVAR_ROZDILNA_TEPLOTA = etree.fromstring("""
<interface-information>
  <physical-interface>
    <name>et-0/0/12</name>
    <optics-diagnostics>
      <module-temperature celsius="30">30 degrees C / 86 degrees F</module-temperature>
      <optics-diagnostics-lane-values>
        <lane-index>0</lane-index>
        <laser-rx-optical-power-dbm>-4.0</laser-rx-optical-power-dbm>
        <laser-temperature celsius="45">45 degrees C / 113 degrees F</laser-temperature>
      </optics-diagnostics-lane-values>
    </optics-diagnostics>
  </physical-interface>
</interface-information>
""")


def test_per_lane_teplota_ma_prednost_pred_modulovou():
    """Realny EVO fixture ma per-lane a modulovou teplotu shodne (obe 0), coz
    by masklo regresi 'per-lane se nikdy neprecte'. Tenhle test je rozlisi:
    modul 30, lane 45 - vysledek musi byt 45, ne 30.
    """
    result = OpticsCollector().parse(EVO_TVAR_ROZDILNA_TEPLOTA, "junos-evo")
    lane = result["et-0/0/12"]["lanes"][0]
    assert lane["temperature_c"] == 45.0


def test_realne_evo_xml(rpc_fixture):
    xml = rpc_fixture("junos-evo", "optics")  # skip, dokud fixture neni
    result = OpticsCollector().parse(xml, "junos-evo")
    assert result  # aspon jeden port s lanes

    found_true_alarm = False
    found_temperature = False
    for data in result.values():
        for lane in data["lanes"]:
            assert "rx_power_dbm" in lane
            assert isinstance(lane["alarms"], dict)
            assert isinstance(lane["warnings"], dict)
            assert all(isinstance(value, bool) for value in lane["alarms"].values())
            assert all(isinstance(value, bool) for value in lane["warnings"].values())
            if any(lane["alarms"].values()):
                found_true_alarm = True
            if lane["temperature_c"] is not None:
                found_temperature = True

    # Realny fixture ma nepripojene porty - RX-low/LOS alarmy jsou tam
    # genuinne zvednute, takze test_all_false by lhal o datech z labu.
    assert found_true_alarm
    # Overuje jen, ze temperature_c neni tise porad None - odkud presne
    # hodnota prisla (per-lane vs modulovy fallback) v tomto fixture
    # nerozlisi, protoze obe se shoduji (celsius="0" vsude). To pokryva
    # test_per_lane_teplota_ma_prednost_pred_modulovou vyse.
    assert found_temperature
