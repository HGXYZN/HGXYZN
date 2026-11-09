param([string]$SourceFile,[string]$PdfFile,[string]$Family)
$ErrorActionPreference='Stop'
$app=$null
$doc=$null
$oldLinks=$null
$oldSecurity=$null
$oldAlerts=$null
try {
    switch ($Family) {
        'Word' {
            $app=New-Object -ComObject Word.Application
            $app.Visible=$false
            $app.DisplayAlerts=0
            $app.AutomationSecurity=3
            $oldLinks=$app.Options.UpdateLinksAtOpen
            $app.Options.UpdateLinksAtOpen=$false
            $doc=$app.Documents.Open($SourceFile,$false,$true,$false)
            $doc.ExportAsFixedFormat($PdfFile,17)
        }
        'Excel' {
            $app=New-Object -ComObject Excel.Application
            $app.Visible=$false
            $app.DisplayAlerts=$false
            $app.AutomationSecurity=3
            $app.EnableEvents=$false
            $app.AskToUpdateLinks=$false
            $doc=$app.Workbooks.Open($SourceFile,0,$true)
            $doc.ExportAsFixedFormat(0,$PdfFile)
        }
        'PowerPoint' {
            $app=New-Object -ComObject PowerPoint.Application
            $oldAlerts=$app.DisplayAlerts
            $oldSecurity=$app.AutomationSecurity
            $app.DisplayAlerts=1
            $app.AutomationSecurity=3
            $doc=$app.Presentations.Open($SourceFile,-1,0,0)
            $doc.SaveAs($PdfFile,32)
        }
    }
} catch { exit 1 } finally {
    if ($doc) {
        try { if ($Family -eq 'PowerPoint') { $doc.Close() } else { $doc.Close(0) } } catch {}
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($doc)
    }
    if ($app) {
        try {
            if ($null -ne $oldLinks) { $app.Options.UpdateLinksAtOpen=$oldLinks }
            if ($null -ne $oldSecurity) { $app.AutomationSecurity=$oldSecurity }
            if ($null -ne $oldAlerts) { $app.DisplayAlerts=$oldAlerts }
            $canQuit=switch ($Family) {
                'Word' { $app.Documents.Count -eq 0 }
                'Excel' { $app.Workbooks.Count -eq 0 }
                'PowerPoint' { $app.Presentations.Count -eq 0 }
            }
            if ($canQuit) { $app.Quit() }
        } catch {}
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($app)
    }
}
