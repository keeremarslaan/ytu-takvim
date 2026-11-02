from ytu_scheduler.models import RequirementKind, Season
from ytu_scheduler.services.bologna import normalize_course_code, parse_curriculum

FIXTURE = """
<html><body><div id="curriculum"><table>
  <tr class="table_semester_heading"><th colspan="8">2.Yıl - Güz Yarıyılı</th></tr>
  <tr class="semester_heading_course"><th>Kodu</th></tr>
  <tr>
    <td><a href="/index.php?r=course/view&amp;id=150&amp;aid=11&amp;pid=9">IST2121</a></td>
    <td>MAT1001</td><td>Matematiksel İstatistiğe Giriş</td>
    <td>3</td><td>0</td><td>0</td><td>3</td><td>5</td>
  </tr>
  <tr>
    <td class="2545"><a class="elective"
      href="/index.php?r=course/view&amp;id=&amp;aid=11&amp;pid=9">MES1-2G</a></td>
    <td></td><td>Mesleki Seçmeli 1-1</td>
    <td>3</td><td>0</td><td>0</td><td>3</td><td>5</td>
  </tr>
  <tr class="table_semester_heading"><th colspan="8"
    class="elective_course_section-2545">Mesleki Seçmeli 1 Dersleri</th></tr>
  <tr>
    <td><a href="/index.php?r=course/view&amp;id=12264&amp;aid=11&amp;pid=9">IST3557</a></td>
    <td></td><td>İstatistik ve Bilimsel Düşünme</td>
    <td>3</td><td>0</td><td>0</td><td>3</td><td>5</td>
  </tr>
</table></div></body></html>
"""


def test_parses_semester_requirements_and_elective_pool() -> None:
    result = parse_curriculum(FIXTURE)

    assert set(result.courses) == {150, 12264}
    assert result.courses[150].code == "IST2121"
    assert result.pools["2545"].course_source_ids == [12264]

    compulsory, elective = result.requirements
    assert compulsory.kind == RequirementKind.COMPULSORY
    assert compulsory.year == 2
    assert compulsory.season == Season.FALL
    assert compulsory.semester_number == 3
    assert compulsory.course_source_id == 150
    assert elective.kind == RequirementKind.ELECTIVE_SLOT
    assert elective.pool_source_id == "2545"
    assert elective.slot_code == "MES1-2G"


def test_normalizes_course_code() -> None:
    assert normalize_course_code(" FIZ 3431 ") == "FIZ3431"
