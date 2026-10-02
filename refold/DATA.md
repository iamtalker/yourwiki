# 되접기 작업 데이터 받는 곳

틀 되접기(2.0 데이터)를 만들 때 쓴 **중간 산출물**입니다. 키트를 설치해서 쓰는 사람에게는 필요 없고, 되접기를 다시 하거나 틀을 더 늘리려는 개발자만 받으면 됩니다.
용량이 커서 저장소와 릴리스 zip 에는 넣지 않고, 데이터 판과 같은 Hugging Face 데이터셋 저장소의 `refold/` 폴더에 두었습니다.

- 폴더: https://huggingface.co/datasets/iamtalker/yourwiki-namumark-20260829/tree/main/refold
- 설명서: https://huggingface.co/datasets/iamtalker/yourwiki-namumark-20260829/blob/main/refold/README.md

| 파일 | 크기(바이트) | 내용 | 직접 받기 |
|---|---:|---|---|
| `blocks.jsonl.gz` | 10,016,266 | 반복 덩어리 8,189종의 본문·나온 문서 수·2021 판 틀 이름 투표 | https://huggingface.co/datasets/iamtalker/yourwiki-namumark-20260829/resolve/main/refold/blocks.jsonl.gz |
| `inc2021.jsonl.gz` | 6,338,715 | 2021 공식 덤프에서 문서마다 불러 쓴 틀 이름(문서 330,312개) | https://huggingface.co/datasets/iamtalker/yourwiki-namumark-20260829/resolve/main/refold/inc2021.jsonl.gz |
| `param_cands.json` | 1,775,388 | 한 줄짜리 매개변수 틀 후보(뼈대 3,484종) | https://huggingface.co/datasets/iamtalker/yourwiki-namumark-20260829/resolve/main/refold/param_cands.json |
| `param_cands_units.json` | 80,125 | 상자·표 전체의 뼈대(69종) | https://huggingface.co/datasets/iamtalker/yourwiki-namumark-20260829/resolve/main/refold/param_cands_units.json |

받아서 이 `refold/` 폴더(작업 폴더의 `refold/`)에 두면 `name_blocks.py`·`refold_run.py` 가 바로 읽습니다. 만든 순서와 결정은 `PLAN.md` 를 보세요.

## 라이선스
`blocks.jsonl.gz` 는 나무위키 문서 본문의 일부이므로 데이터 판과 같은 **CC BY-NC-SA 2.0 KR**(상업적 이용 금지, 저작자 표시, 동일조건변경허락)입니다. 저작권은 각 문서의 기여자에게 있습니다.
나머지는 제목·틀 이름 같은 사실 정보와 집계입니다. 나무위키 운영사와 관계없는 비공식 데이터입니다. 이 저장소의 코드는 MIT 입니다.
