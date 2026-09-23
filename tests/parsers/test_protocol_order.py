"""Determinismus poradi `protocol` (task 14a, R10).

`_collect_protocols` skladal seznam protokolu z `global_protocols_by_interface`,
coz je `dict[str, set[str]]` - poradi iterace stringoveho setu zavisi na
PYTHONHASHSEED procesu. Stejny config tak mohl dat jiny `protocol` seznam
(napr. ['pim', 'igmp'] vs ['igmp', 'pim']) v kazdem samostatnem behu, a
`mig-validate upgrade --dry-run` na nezmenenem capture hlasil spurious
zmenu. Test bezi parser v podprocesech s ruznymi PYTHONHASHSEED a overuje,
ze vysledny seznam je vzdy stejny.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

SEEDS = [str(seed) for seed in range(10)]

# Jeden logicky unit se třemi globálními protokoly (igmp, l2circuit, pim)
# napojenymi pres global_protocols_by_interface - presne ta cesta, kterou
# _collect_protocols sklada druhym a tretim extend().
CONFIG = """
<configuration>
  <interfaces>
    <interface>
      <name>ge-0/0/2</name>
      <unit>
        <name>0</name>
        <family>
          <inet>
            <address><name>10.1.2.1/31</name></address>
          </inet>
        </family>
      </unit>
    </interface>
  </interfaces>
  <protocols>
    <l2circuit>
      <neighbor>
        <name>10.0.0.1</name>
        <interface>
          <name>ge-0/0/2.0</name>
        </interface>
      </neighbor>
    </l2circuit>
    <igmp>
      <interface>
        <name>ge-0/0/2.0</name>
      </interface>
    </igmp>
    <pim>
      <interface>
        <name>ge-0/0/2.0</name>
      </interface>
    </pim>
  </protocols>
</configuration>
"""

PROBE = """
import json
from lxml import etree
from migration_validator.parsers.mx import JunosServiceParser

config = etree.fromstring(CONFIG)
services = JunosServiceParser(config).parse()
by_iface = {s.interface: s for s in services}
print(json.dumps(by_iface["ge-0/0/2.0"].protocol))
""".replace("CONFIG", f"'''{CONFIG}'''")

EXPECTED = ["inet", "igmp", "l2circuit", "pim"]


@pytest.mark.parametrize("seed", SEEDS)
def test_protocol_order_is_deterministic_across_hash_seeds(seed):
    env = {**os.environ, "PYTHONHASHSEED": seed}
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        env=env,
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        check=True,
    )
    protocol = json.loads(result.stdout)

    assert protocol == EXPECTED, (
        f"PYTHONHASHSEED={seed} dal {protocol!r}, ocekavano {EXPECTED!r}"
    )
