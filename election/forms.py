from django import forms

from .models import ElectionCycle


class MemberCsvImportForm(forms.Form):
    cycle = forms.ModelChoiceField(
        queryset=ElectionCycle.objects.order_by("-year"), label="選挙年度"
    )
    csv_file = forms.FileField(
        label="会員名簿CSV",
        help_text="UTF-8またはCP932（ExcelのCSV）に対応しています。",
    )

    def clean_csv_file(self):
        uploaded_file = self.cleaned_data["csv_file"]
        if not uploaded_file.name.lower().endswith(".csv"):
            raise forms.ValidationError("CSVファイルを選択してください。")
        return uploaded_file
