"""管理画面で共通のダウンロードレスポンス。"""
import csv

from django.http import HttpResponse


def result_csv_response(export):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{export.filename}"'
    response.write("\ufeff")
    writer = csv.DictWriter(response, fieldnames=export.fieldnames)
    writer.writeheader()
    writer.writerows(export.rows)
    return response
