# uibridge test harness: a small WinForms window for the Phase 7 matrix (WinForms baseline + classic Open dialog).
# Run: powershell.exe -NoProfile -STA -File tests\apps\winforms_demo.ps1
# Nothing is written anywhere; "Open file..." only shows the dialog and reports the chosen name.
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()

$form = New-Object System.Windows.Forms.Form
$form.Text = "uibridge WinForms demo"
$form.Width = 420; $form.Height = 360

$nameLabel = New-Object System.Windows.Forms.Label
$nameLabel.Text = "Name:"; $nameLabel.Left = 12; $nameLabel.Top = 15; $nameLabel.Width = 60
$name = New-Object System.Windows.Forms.TextBox
$name.Name = "nameBox"; $name.Left = 80; $name.Top = 12; $name.Width = 300

$agree = New-Object System.Windows.Forms.CheckBox
$agree.Name = "agreeBox"; $agree.Text = "I agree"; $agree.Left = 80; $agree.Top = 42; $agree.Width = 200

$fruits = New-Object System.Windows.Forms.ListBox
$fruits.Name = "fruitList"; $fruits.Left = 80; $fruits.Top = 72; $fruits.Width = 140; $fruits.Height = 60
[void]$fruits.Items.AddRange(@("Apple", "Banana", "Cherry"))

$size = New-Object System.Windows.Forms.ComboBox
$size.Name = "sizeCombo"; $size.Left = 240; $size.Top = 72; $size.Width = 140
$size.DropDownStyle = [System.Windows.Forms.ComboBoxStyle]::DropDownList
[void]$size.Items.AddRange(@("Small", "Medium", "Large")); $size.SelectedIndex = 0

$status = New-Object System.Windows.Forms.Label
$status.Name = "statusLabel"; $status.Text = "Status: idle"; $status.Left = 80; $status.Top = 150; $status.Width = 300

$apply = New-Object System.Windows.Forms.Button
$apply.Name = "applyButton"; $apply.Text = "Apply"; $apply.Left = 80; $apply.Top = 180; $apply.Width = 100
$apply.Add_Click({
    $status.Text = "Status: applied name=" + $name.Text + " agree=" + $agree.Checked + " fruit=" + $fruits.SelectedItem + " size=" + $size.SelectedItem
})

$open = New-Object System.Windows.Forms.Button
$open.Name = "openButton"; $open.Text = "Open file..."; $open.Left = 190; $open.Top = 180; $open.Width = 100
$open.Add_Click({
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = "Open (uibridge test)"
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { $status.Text = "Status: chose " + $dialog.FileName }
    else { $status.Text = "Status: open cancelled" }
})

$form.Controls.AddRange(@($nameLabel, $name, $agree, $fruits, $size, $status, $apply, $open))
[void]$form.ShowDialog()
