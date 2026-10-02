"""유어위키 첫 화면 문서(유어위키:대문)를 넣고, openNAMU 의 첫 화면과 사이트 이름을 유어위키로 정한다.

나무위키 데이터에는 'FrontPage'라는 문서(동음이의어 안내)가 이미 있어서 덮어쓰지 않고 별도 문서를 쓴다.
이미 있으면 그대로 둔다(--force 로 덮어쓰기). 사이트 이름은 기본값(Wiki)일 때만 바꾼다.

사용: python add_frontpage.py <wiki 폴더> [--force]
"""
import os
import sqlite3
import sys
import time

TITLE = "유어위키:대문"


def set_setting(db, name, value, only_if_default=None):
    cur = db.execute("select data from other where name = ? and coverage = ''", (name,)).fetchone()
    if only_if_default is not None and cur and cur[0] not in ("", only_if_default):
        return
    db.execute("delete from other where name = ? and coverage = ''", (name,))
    db.execute("insert into other (name, data, coverage) values (?, ?, '')", (name, value))
TEXT = """[목차]
== 유어위키에 오신 것을 환영합니다 ==
'''유어위키(YourWiki)'''는 나무위키의 글을 누구나 자기 컴퓨터에 가질 수 있게 해 주는 비공식 도구로 만든 위키입니다.
나무위키 문서 약 180만 개가 들어 있고, 편집·추가·삭제도 할 수 있습니다.

{{{#!wiki style="border:1px solid #cc8800; padding:8px 12px; border-radius:6px"
'''이 데이터는 CC BY-NC-SA 2.0 KR입니다. 상업적 이용은 금지됩니다.'''
이 키트를 사용해 광고를 붙이거나 상업적으로 운영하는 것은 라이선스 위반입니다.
}}}

== 유어위키로 할 수 있는 것 ==
||<tablewidth=100%><tablebordercolor=#3b5bdb><bgcolor=#3b5bdb><color=#fff><width=22%> '''기능''' ||<bgcolor=#3b5bdb><color=#fff> '''설명''' ||
|| '''원터치 설치''' || `유어위키.exe` 하나로 나무위키 문서 약 180만 개가 든 위키를 설치하고 켜고 끕니다. ||
|| '''편집''' || 진짜 위키입니다. 문서를 고치고, 새로 만들고, 지울 수 있습니다. ||
|| '''최신판 동기화''' || 위키가 켜져 있는 동안 나무위키 최근 변경을 따라 문서를 자동으로 갱신합니다. ||
|| '''갱신 단추''' || 화면 왼쪽 아래 「🔄 나무위키 최신판으로 갱신」을 누르면 그 문서를 바로 새로 받아 옵니다. 최근에 확인한 문서는 「✔ 최신 버전」으로 표시됩니다. ||
|| '''검색''' || 검색창에 글자를 치면 그 글자로 시작하는 문서가 아래에 뜹니다. 🔍는 검색, →는 바로 가기, 🔀는 아무 문서나 보기입니다. ||
|| '''인터넷에 공개''' || 관리판의 [공개하기]를 누르면 공유기 설정 없이 공개 주소가 생깁니다. 이 주소는 '''임시 주소'''라 켤 때마다 무작위로 바뀌고, 끄면 사라집니다(고정 주소가 필요하면 서버에 설치하세요). 리눅스 서버용 설치 스크립트도 있습니다. ||
|| '''내 마음대로''' || 관리판에서 위키 색을 고를 수 있습니다. 이 대문도 자유롭게 고쳐 쓰세요. ||
|| '''오프라인''' || 설치한 뒤에는 인터넷 없이도 동작하고, 문서를 볼 때 외부 서비스로 접속하지 않습니다. ||

== 둘러보기 ==
 * 위쪽 '''검색창'''에 문서 이름을 입력해 찾아보세요.
 * 예: [[대한민국]] · [[위키]] · [[한국어]] · [[컴퓨터]] · [[음식]]

== 나무위키 최신판으로 갱신 ==
 * 이 위키의 문서는 2026-08-29 무렵 나무위키 기준입니다.
 * 문서 위쪽의 '''「🔄 나무위키 최신판으로 갱신」''' 단추를 누르면 그 문서를 나무위키에서 새로 받아 옵니다.
 * 위키를 켜 두면 나무위키 최근 변경을 따라 문서가 자동으로 갱신됩니다(관리판에서 끌 수 있습니다).
 * 나무위키 서버에 부담을 주지 않도록 robots.txt 를 지키고 6초에 1건 이하로만 요청하며, 차단되면 즉시 멈춥니다.

== 출처와 저작권 ==
 * 모든 문서의 저작권은 각 기여자에게 있습니다. 문서마다 끝에 원 나무위키 주소와 라이선스가 적혀 있고, 기여자 목록은 원 문서의 역사에서 볼 수 있습니다.
 * 라이선스: [[https://creativecommons.org/licenses/by-nc-sa/2.0/kr/|CC BY-NC-SA 2.0 KR]] (저작자 표시 · 비영리 · 동일조건 변경 허락)
 * 이미지는 들어 있지 않습니다.

== 유어위키에 대해 ==
 * 유어위키는 나무위키 운영사와 관계없는 비공식 도구입니다.
 * 키트와 사용법: [[https://github.com/iamtalker/yourwiki|github.com/iamtalker/yourwiki]]
 * 이 첫 화면은 키트가 만든 문서입니다. 운영자가 자유롭게 고쳐 쓰셔도 됩니다.
 * 나무위키의 FrontPage 문서는 [[FrontPage]]에 그대로 있습니다.
"""


def main(wiki_dir, force=False):
    db = sqlite3.connect(os.path.join(wiki_dir, "data.db"), timeout=60)
    set_setting(db, "frontpage", TITLE)
    set_setting(db, "name", "유어위키", only_if_default="Wiki")
    db.commit()
    if db.execute("select 1 from data where title = ?", (TITLE,)).fetchone() and not force:
        print("첫 화면 문서가 이미 있습니다(첫 화면·사이트 이름 설정은 확인함)")
        return
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    db.execute("delete from data where title = ?", (TITLE,))
    db.execute("insert into data (title, data, type) values (?, ?, '')", (TITLE, TEXT))
    rev = (db.execute("select max(id + 0) from history where title = ?", (TITLE,)).fetchone()[0] or 0) + 1
    db.execute("insert into history (id, title, data, date, ip, send, leng, hide, type) "
               "values (?, ?, ?, ?, '유어위키 키트', '유어위키 첫 화면', ?, '', ?)",
               (str(rev), TITLE, TEXT, now, str(len(TEXT)), "r1" if rev == 1 else ""))
    db.execute("delete from data_set where doc_name = ? and set_name in ('last_edit', 'length')", (TITLE,))
    db.executemany("insert into data_set (doc_name, doc_rev, set_name, set_data) values (?, '', ?, ?)",
                   [(TITLE, "last_edit", now), (TITLE, "length", str(len(TEXT)))])
    db.commit()
    print("첫 화면 문서를 넣었습니다")


if __name__ == "__main__":
    main(sys.argv[1], "--force" in sys.argv)
