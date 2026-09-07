"""Prepare the current monthly layout directly through Sheets, before Apps Script.

Only recognized attendance headers may be changed. Existing records are moved by
inserting an input row, never copied, cleared, or deleted. A successful write is
not readiness: the actual workbook must be read again.
"""
from __future__ import annotations

import json
import zipfile
import xml.etree.ElementTree as ET
from attendance_chat_marker import CHAT_RESULT_HEADERS, LEGACY_CHAT_RESULT_HEADERS
from brity_bridge import process_win

LAYOUT_VERSION = 'monthly-ai-chat-1'
MONTHS = tuple(f'{m}월' for m in (*range(3, 13), 1, 2))
HEADERS = ('날짜','번호+이름','구분','종류','사유','교시','신고서','첨부') + CHAT_RESULT_HEADERS + ('AI 입력',)
AI_LABEL = 'AI 출결 입력'
AI_HINT = '여기에 "3월 12일 김철수 병결" 처럼 적고 Enter를 누르세요'
STUDENT_RANGE = "='드롭다운'!$J$2:$J$200"
STUDENT_FORMULA = '=IF(AND(\'학생명단\'!A2<>"",\'학생명단\'!B2<>""),\'학생명단\'!A2&\'학생명단\'!B2,"")'

USAGE_TITLE = 'Teacher Manager 출결 사용 안내'
PREVIOUS_USAGE_TITLE = '출결 신고서 자동화 사용 순서 — 기존 Google Docs 템플릿 그대로 사용'
USAGE_ROWS = [['순서', '작업', '설명', '', '', ''], ['1', '처음 설정', 'Teacher Manager에서 출결 준비를 마친 뒤, 출석부 메뉴 [처음 한 번 설정하기 → 처음 설정 한 번에 끝내기]를 실행합니다.', '', '', ''], ['2', '학생명단', '학생명단에 번호, 이름, 학생 Google 이메일을 각각 입력합니다.', '', '', ''], ['3', '월별 출결 입력', '각 월의 맨 위 AI 출결 입력칸에 문장을 적거나, 3행부터 날짜·학생·구분·종류·사유를 입력합니다.', '', '', ''], ['4', '학생 선택', '월별 출결표의 학생 선택목록은 학생명단의 번호와 이름으로 만들어집니다.', '', '', ''], ['5', '신고서', '출결표에서 행을 선택하고 출결 신고서 만들기 메뉴를 사용합니다.', '', '', ''], ['6', 'Google Chat', '발송상태·발송시각·결과를 월별 출결표에서 확인합니다.', '', '', ''], ['7', '안내와 할 일', '메신저 개인톡 내용과 메신저 단체톡 내용에서 안내를 확인하고 출결 미제출 할 일을 관리합니다.', '', '', ''], ['8', '휴일', '휴일 탭에서 학교의 수업일과 휴무일을 확인합니다.', '', '', '']]


def validate_template(path):
    """Reject obsolete creation assets, including inside a completed installer."""
    ns = {'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    try:
        with zipfile.ZipFile(path) as archive:
            strings = ([''.join(item.itertext()) for item in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
                       if 'xl/sharedStrings.xml' in archive.namelist() else [])
            for index in range(1,13):
                root = ET.fromstring(archive.read(f'xl/worksheets/sheet{index}.xml'))
                cells = {c.get('r'):(strings[int(c.find('s:v',ns).text)] if c.get('t')=='s' else ''.join(c.itertext()))
                         for c in root.findall('.//s:sheetData/s:row/s:c',ns)}
                if (cells.get('A1') != AI_LABEL or [cells.get(chr(65+i)+'2') for i in range(13)] != list(HEADERS)
                        or root.find('s:dimension',ns).get('ref') != 'A1:M250'
                        or not any(m.get('ref')=='B1:M1' for m in root.findall('s:mergeCells/s:mergeCell',ns))):
                    raise ValueError('obsolete monthly layout')
    except (OSError, KeyError, ValueError, AttributeError, ET.ParseError, zipfile.BadZipFile) as error:
        raise ValueError('설치 자료에 최신 출결 양식이 없어 제작을 중단했습니다.') from error
    return True


def _formula_key(value):
    return str(value or '').replace("'",'').replace('$','').replace(' ','').lstrip('=').casefold()


def _color(hex_value):
    return dict(zip(('red','green','blue'), (int(hex_value[i:i+2],16)/255 for i in (0,2,4))))


def _cells(sheet, row):
    for block in sheet.get('data', []):
        offset = row - int(block.get('startRow', 0))
        rows = block.get('rowData', [])
        if int(block.get('startColumn', 0)) == 0 and 0 <= offset < len(rows):
            return rows[offset].get('values', [])
    return []


def _values(sheet, row):
    result = []
    for cell in _cells(sheet,row):
        value = cell.get('userEnteredValue', {})
        result.append(str(value.get('stringValue', value.get('formulaValue', value.get('numberValue','')))).strip())
    return (result + [''] * 13)[:13]


def _header_kind(sheet):
    row0, row1 = _values(sheet,0), _values(sheet,1)
    if row1[:8] == list(HEADERS[:8]):
        header, kind = row1, 'current'
    elif row0[:8] == list(HEADERS[:8]):
        header, kind = row0, 'one-header-row'
    else:
        raise ValueError('출결표의 제목을 확인하지 못했어요. 기존 자료는 바꾸지 않았습니다.')
    aliases = LEGACY_CHAT_RESULT_HEADERS + ('AI 입력',)
    for i in range(8,13):
        if header[i] not in ('', HEADERS[i], HEADERS[i].replace('\n',' '), aliases[i-8]):
            raise ValueError('출결표에 직접 추가한 제목이 있어 자동으로 덮어쓰지 않았어요.')
    if kind == 'current' and (row0[0] not in ('', AI_LABEL, 'AI 출결 입력 (준비 중)')
                              or any(row0[2:])):
        raise ValueError('출결 입력칸에 기존 내용이 있어 합치지 않았어요. 자료는 그대로입니다.')
    return kind


def _range(sheet_id, row, end_row, col=0, end_col=13):
    return {'sheetId':sheet_id,'startRowIndex':row,'endRowIndex':end_row,
            'startColumnIndex':col,'endColumnIndex':end_col}


def _is_current(sheet):
    props = sheet['properties']; sid = props['sheetId']
    data = _cells(sheet,2)
    student_rule = (data[1].get('dataValidation',{}).get('condition',{}) if len(data)>1 else {})
    student_values = student_rule.get('values',[])
    hidden = set()
    for block in sheet.get('data',[]):
        hidden.update(int(block.get('startColumn',0))+i for i,item in enumerate(block.get('columnMetadata',[]))
                      if item.get('hiddenByUser'))
    return (_values(sheet,0)[0] == AI_LABEL and _values(sheet,1) == list(HEADERS)
            and props.get('gridProperties',{}).get('frozenRowCount') == 2
            and student_rule.get('type') == 'ONE_OF_RANGE' and len(student_values)==1
            and _formula_key(student_values[0].get('userEnteredValue')) == _formula_key(STUDENT_RANGE)
            and {6,11}.issubset(hidden)
            and any(all(m.get(k,0)==v for k,v in _range(sid,0,1,1,13).items())
                    for m in sheet.get('merges',[])))


def _tail_header_repairs(sheet, old_header):
    """Remove only the shipped navy fill from empty N:P header cells."""
    sid = sheet['properties']['sheetId']
    requests = []
    navy = _color('1F4E79')
    for block in sheet.get('data', []):
        for offset, row in enumerate(block.get('rowData', [])):
            row_index = int(block.get('startRow', 0)) + offset
            if row_index > (0 if old_header else 1):
                continue
            for index, cell in enumerate(row.get('values', [])):
                col = int(block.get('startColumn', 0)) + index
                if not 13 <= col < 16 or cell.get('userEnteredValue') or cell.get('note'):
                    continue
                fmt = cell.get('userEnteredFormat', {})
                color = fmt.get('backgroundColorStyle', {}).get('rgbColor', fmt.get('backgroundColor', {}))
                if not all(abs(color.get(k, -1) - v) < .001 for k, v in navy.items()):
                    continue
                # Inserting the AI row inherits the original blank header's fill.
                start, end = (0, 2) if old_header else (row_index, row_index + 1)
                requests.append({'repeatCell': {'range': _range(sid, start, end, col, col + 1),
                    'cell': {'userEnteredFormat': {'backgroundColor': _color('FFFFFF'),
                                                  'backgroundColorStyle': {'rgbColor': _color('FFFFFF')}}},
                    'fields': 'userEnteredFormat.backgroundColor,userEnteredFormat.backgroundColorStyle'}})
    return requests


def _usage_title_repairs(guide):
    """Format only a recognized title; merging must never discard teacher content."""
    if not guide or _values(guide, 0)[0] not in (USAGE_TITLE, PREVIOUS_USAGE_TITLE):
        return None
    if any(cell.get('userEnteredValue', {}) not in ({}, {'stringValue': ''}) or cell.get('note') for cell in _cells(guide, 0)[1:3]):
        return None
    sid = guide['properties']['sheetId']
    target = _range(sid, 0, 1, 0, 3)
    previous = _range(sid, 0, 1, 0, 6)
    overlaps = [m for m in guide.get('merges', [])
                if m.get('startRowIndex', 0) == 0 and m.get('startColumnIndex', 0) < 3]
    def matches(merge, area):
        return all(merge.get(key, 0) == value for key, value in area.items())
    if any(not matches(m, target) and not matches(m, previous) for m in overlaps):
        return None
    requests = []
    if not any(matches(m, target) for m in overlaps):
        for merge in overlaps:
            requests.append({'unmergeCells': {'range': merge}})
        requests.append({'mergeCells': {'range': target, 'mergeType': 'MERGE_ALL'}})
    cell = _cells(guide, 0)[0]
    fmt = cell.get('userEnteredFormat', {})
    text = fmt.get('textFormat', {})
    def rgb(value):
        return tuple(round(value.get(key, 0) * 255) for key in ('red', 'green', 'blue'))
    styled = (fmt.get('horizontalAlignment') == 'CENTER' and fmt.get('verticalAlignment') == 'MIDDLE'
              and fmt.get('wrapStrategy') == 'WRAP' and text.get('bold') is True and text.get('fontSize') == 14
              and rgb(fmt.get('backgroundColorStyle', {}).get('rgbColor', fmt.get('backgroundColor', {}))) == (31, 78, 121)
              and rgb(text.get('foregroundColorStyle', {}).get('rgbColor', text.get('foregroundColor', {}))) == (255, 255, 255))
    if requests or not styled:
        title_format = {'backgroundColor': _color('1F4E79'), 'backgroundColorStyle': {'rgbColor': _color('1F4E79')},
                        'horizontalAlignment': 'CENTER', 'verticalAlignment': 'MIDDLE', 'wrapStrategy': 'WRAP',
                        'textFormat': {'bold': True, 'fontSize': 14, 'foregroundColor': _color('FFFFFF'),
                                       'foregroundColorStyle': {'rgbColor': _color('FFFFFF')}}}
        requests.append({'repeatCell': {'range': target, 'cell': {'userEnteredFormat': title_format},
                                       'fields': ','.join('userEnteredFormat.' + key for key in title_format)}})
        requests.append({'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'ROWS', 'startIndex': 0, 'endIndex': 1},
                                                       'properties': {'pixelSize': 36}, 'fields': 'pixelSize'}})
    return requests


def plan_layout(snapshot):
    sheets = {s.get('properties',{}).get('title'):s for s in snapshot.get('sheets',[])}
    if any(name not in sheets for name in (*MONTHS, '드롭다운', '학생명단')):
        raise ValueError('출결표의 월별 탭이나 학생 선택목록을 끝까지 확인하지 못했어요.')
    # Validate all months before producing any mutating request.
    kinds = {name:_header_kind(sheets[name]) for name in MONTHS}
    todo = [name for name in MONTHS if not _is_current(sheets[name])]
    requests = []
    dropdown_sheet = sheets['드롭다운']
    helper_found = False
    for block in dropdown_sheet.get('data',[]):
        values = block.get('rowData',[])
        if block.get('startColumn',0)==9 and block.get('startRow',0)==0 and len(values)>1:
            first = values[0].get('values',[]); second = values[1].get('values',[])
            helper_found = (bool(first) and bool(second)
                and first[0].get('userEnteredValue',{}).get('stringValue')=='학생_번호이름'
                and _formula_key(second[0].get('userEnteredValue',{}).get('formulaValue'))==_formula_key(STUDENT_FORMULA))
    if not helper_found:
        dropdown = dropdown_sheet['properties']['sheetId']
        grid = dropdown_sheet['properties'].get('gridProperties',{})
        requests.append({'updateSheetProperties':{'properties':{'sheetId':dropdown,'gridProperties':{
            'rowCount':max(grid.get('rowCount',200),200),'columnCount':max(grid.get('columnCount',10),10)}},'fields':'gridProperties.rowCount,gridProperties.columnCount'}})
        requests.append({'updateCells':{'start':{'sheetId':dropdown,'rowIndex':0,'columnIndex':9},
            'rows':[{'values':[{'userEnteredValue':{'stringValue':'학생_번호이름'}}]}],'fields':'userEnteredValue'}})
        requests.append({'repeatCell':{'range':_range(dropdown,1,200,9,10),
            'cell':{'userEnteredValue':{'formulaValue':STUDENT_FORMULA}},'fields':'userEnteredValue'}})
        requests.append({'updateDimensionProperties':{'range':{'sheetId':dropdown,'dimension':'COLUMNS','startIndex':9,'endIndex':10},
            'properties':{'hiddenByUser':True},'fields':'hiddenByUser'}})
    guide = sheets.get('00_사용법')
    guide_title_requests = _usage_title_repairs(guide)
    if guide_title_requests is not None and _values(guide, 0)[0] == PREVIOUS_USAGE_TITLE:
        guide_id = guide['properties']['sheetId']
        values = [[USAGE_TITLE] + [''] * 5] + USAGE_ROWS + [[''] * 6 for _ in range(4)]
        requests.append({'updateCells': {'start': {'sheetId': guide_id, 'rowIndex': 0, 'columnIndex': 0},
            'rows': [{'values': [{'userEnteredValue': {'stringValue': value}} for value in row]} for row in values],
            'fields': 'userEnteredValue'}})
    requests.extend(guide_title_requests or [])
    for name in todo:
        sheet = sheets[name]; props = sheet['properties']; sid = props['sheetId']
        rows = max(int(props.get('gridProperties',{}).get('rowCount',250)),250)
        columns = max(int(props.get('gridProperties',{}).get('columnCount',13)),13)
        if kinds[name] == 'one-header-row':
            requests.append({'insertDimension':{'range':{'sheetId':sid,'dimension':'ROWS','startIndex':0,'endIndex':1},'inheritFromBefore':False}})
            rows += 1
        requests.append({'updateSheetProperties':{'properties':{'sheetId':sid,'gridProperties':{
            'rowCount':rows,'columnCount':columns,'frozenRowCount':2}},'fields':'gridProperties.rowCount,gridProperties.columnCount,gridProperties.frozenRowCount'}})
        # Values are written to the two header rows only. Attendance rows remain intact.
        requests.append({'updateCells':{'start':{'sheetId':sid,'rowIndex':0,'columnIndex':0},
            'rows':[{'values':[{'userEnteredValue':{'stringValue':AI_LABEL}}]}], 'fields':'userEnteredValue'}})
        requests.append({'updateCells':{'start':{'sheetId':sid,'rowIndex':1,'columnIndex':0},
            'rows':[{'values':[{'userEnteredValue':{'stringValue':v}} for v in HEADERS]}], 'fields':'userEnteredValue'}})
        box = _range(sid,0,1,1,13)
        if not any(all(m.get(k,0)==v for k,v in box.items()) for m in sheet.get('merges',[])):
            # An unrelated merge is not ours to unmerge.
            if kinds[name] == 'current' and any(m.get('startRowIndex',0)==0 for m in sheet.get('merges',[])):
                raise ValueError('출결 입력줄의 합쳐진 칸을 안전하게 확인하지 못했어요.')
            requests.append({'mergeCells':{'range':box,'mergeType':'MERGE_ALL'}})
        def paint(area, fmt):
            requests.append({'repeatCell':{'range':area,'cell':{'userEnteredFormat':fmt},
                'fields':','.join('userEnteredFormat.'+key for key in fmt)}})
        paint(_range(sid,0,rows),{'verticalAlignment':'MIDDLE','wrapStrategy':'WRAP'})
        paint(_range(sid,1,2),{'backgroundColor':_color('1F4E79'),'horizontalAlignment':'CENTER',
                              'textFormat':{'bold':True,'foregroundColor':_color('FFFFFF')}})
        paint(_range(sid,0,1),{'backgroundColor':_color('FFFFFF'),'textFormat':{'bold':False,'foregroundColor':_color('000000')}})
        paint(_range(sid,0,1,0,1),{'backgroundColor':_color('E8F2FF'),'textFormat':{'bold':True}})
        paint(_range(sid,2,rows,0,1),{'numberFormat':{'type':'DATE','pattern':'yyyy-mm-dd'}})
        top = _cells(sheet,0)
        if kinds[name] != 'current' or len(top)<2 or not top[1].get('note'):
            requests.append({'repeatCell':{'range':_range(sid,0,1,1,2),'cell':{'note':AI_HINT},'fields':'note'}})
        for start,end,pixels in ((0,1,34),(1,2,40),(2,rows,24)):
            requests.append({'updateDimensionProperties':{'range':{'sheetId':sid,'dimension':'ROWS','startIndex':start,'endIndex':end},'properties':{'pixelSize':pixels},'fields':'pixelSize'}})
        for index,pixels in enumerate((90,120,90,90,220,90,90,90,130,150,360,70,90)):
            requests.append({'updateDimensionProperties':{'range':{'sheetId':sid,'dimension':'COLUMNS','startIndex':index,'endIndex':index+1},'properties':{'pixelSize':pixels, 'hiddenByUser':index in (6,11)},'fields':'pixelSize,hiddenByUser'}})
        rules = ((1,2,'ONE_OF_RANGE',[STUDENT_RANGE],True),(2,3,'ONE_OF_LIST',['질병','미인정','기타','출석인정'],True),
                 (3,4,'ONE_OF_LIST',['결석함','지각함','조퇴함','결과함'],True),
                 (5,6,'ONE_OF_LIST',['1교시','2교시','3교시','4교시','5교시','6교시','7교시','조회','종례'],False),
                 (6,8,'ONE_OF_LIST',['제출','미제출','해당없음'],False))
        for start,end,kind,values,strict in rules:
            requests.append({'setDataValidation':{'range':_range(sid,2,rows,start,end),'rule':{'condition':{'type':kind,'values':[{'userEnteredValue':v} for v in values]},'strict':strict,'showCustomUi':True}}})
        border = {'style':'SOLID','color':_color('A6A6A6')}
        requests.append({'updateBorders':{'range':_range(sid,2,rows), **{k:border for k in ('top','bottom','left','right','innerHorizontal','innerVertical')}}})
        for parity,color in ((1,'BDBDBD'),(0,'FFFFFF')):
            formula = f'=AND($A3<>"",MOD(SUMPRODUCT(($A$3:$A3<>$A$2:$A2)*($A$3:$A3<>"")),2)={parity})'
            requests.append({'addConditionalFormatRule':{'index':0,'rule':{'ranges':[_range(sid,2,rows)],'booleanRule':{'condition':{'type':'CUSTOM_FORMULA','values':[{'userEnteredValue':formula}]},'format':{'backgroundColor':_color(color)}}}}})
    for name in MONTHS:
        requests.extend(_tail_header_repairs(sheets[name], kinds[name] == 'one-header-row'))
    return requests


def ensure_layout(runner, workdir, spreadsheet_id, gws):
    def read():
        params = {'spreadsheetId':spreadsheet_id,'includeGridData':True,
                  'ranges':[f"'{m}'!1:3" for m in MONTHS]+["'드롭다운'!J1:J2","'학생명단'!A1:C1"],
                  'fields':'spreadsheetId,sheets(properties,merges,data(startRow,startColumn,columnMetadata(hiddenByUser),rowData(values(userEnteredValue,note,dataValidation,userEnteredFormat(backgroundColor,backgroundColorStyle)))))'}
        reply = process_win.parse_first_json(runner([gws,'sheets','spreadsheets','get','--params',json.dumps(params,ensure_ascii=False),'--format','json'],workdir))
        if not isinstance(reply,dict) or reply.get('spreadsheetId') != spreadsheet_id:
            raise ValueError('준비하던 출결표와 Google 응답이 일치하지 않아 멈췄어요.')
        guide_reply = process_win.parse_first_json(runner([gws,'sheets','spreadsheets','get','--params',
            json.dumps({'spreadsheetId':spreadsheet_id,'fields':'sheets(properties(sheetId,title))'}),
            '--format','json'], workdir))
        guide_present = any(item.get('properties',{}).get('title') == '00_사용법'
                            for item in (guide_reply or {}).get('sheets', []))
        if guide_present:
            guide_reply = process_win.parse_first_json(runner([gws,'sheets','spreadsheets','get','--params',
                json.dumps({'spreadsheetId':spreadsheet_id,'ranges':["'00_사용법'!A1:F14"],
                            'fields':'sheets(properties,merges,data(startRow,startColumn,rowData(values(userEnteredValue,note,userEnteredFormat))))'},ensure_ascii=False),
                '--format','json'],workdir))
            reply['sheets'] = [item for item in reply['sheets'] if item['properties']['title'] != '00_사용법']
            reply['sheets'].extend((guide_reply or {}).get('sheets', []))
        return reply
    requests = plan_layout(read())
    if requests:
        # Keep each month's insert and formatting in one atomic Sheets request,
        # and keep the Windows command line below its 32,767-character limit.
        groups = []
        for request in requests:
            operation = next(iter(request.values()))
            sid = (operation.get('range') or operation.get('start') or operation.get('properties') or {}).get('sheetId')
            if sid is None and 'rule' in operation:
                sid = operation['rule']['ranges'][0]['sheetId']
            if not groups or groups[-1][0] != sid:
                groups.append((sid, []))
            groups[-1][1].append(request)
        for _, group in groups:
            body = json.dumps({'requests':group},ensure_ascii=False)
            if len(body) > 24000:
                raise RuntimeError('출결 서식 적용 요청이 너무 커서 안전하게 보내지 않았어요.')
            runner([gws,'sheets','spreadsheets','batchUpdate','--params',json.dumps({'spreadsheetId':spreadsheet_id}),
                    '--json',body,'--format','json'],workdir)
        if plan_layout(read()):
            raise RuntimeError('최신 출결 서식이 적용된 것을 확인하지 못했어요. 준비 완료로 표시하지 않았습니다.')
    return True
