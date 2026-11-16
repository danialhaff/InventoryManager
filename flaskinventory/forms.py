from flask_wtf import FlaskForm
from wtforms import StringField,IntegerField,SelectField,SelectMultipleField,SubmitField,DateTimeField
from wtforms.validators import DataRequired,Length,NumberRange,Optional
import datetime



class addproduct(FlaskForm):
    prodname = StringField('Product Name', validators=[DataRequired(), Length(max=20)])
    prodqty = IntegerField('Quantity', validators=[DataRequired(), NumberRange(min=1, max=1000000)])
    threshold = IntegerField('Low stock threshold', validators=[DataRequired(), NumberRange(min=0, max=1000000)])
    prodsubmit = SubmitField('Save Changes')

class editproduct(FlaskForm):
    editname = StringField('Product Name', validators=[DataRequired(), Length(max=20)])
    threshold = IntegerField('Low stock threshold', validators=[DataRequired(), NumberRange(min=0, max=1000000)])
    editsubmit = SubmitField('Save Changes')

class addlocation(FlaskForm):
    locname = StringField('Location Name', validators=[DataRequired(), Length(max=20)])
    locsubmit = SubmitField('Save Changes')

class editlocation(FlaskForm):
    editlocname = StringField('Location Name', validators=[DataRequired(), Length(max=20)])
    editlocsubmit = SubmitField('Save Changes')

class addstaff(FlaskForm):
    staffname = StringField('Staff name', validators=[DataRequired(), Length(max=40)])
    role = StringField('Role', validators=[Optional(), Length(max=40)])
    locations = SelectMultipleField('Assigned locations', coerce=int, validators=[Optional()])
    staffsubmit = SubmitField('Save Changes')

class editstaff(FlaskForm):
    editstaffname = StringField('Staff name', validators=[DataRequired(), Length(max=40)])
    editrole = StringField('Role', validators=[Optional(), Length(max=40)])
    editlocations = SelectMultipleField('Assigned locations', coerce=int, validators=[Optional()])
    editstaffsubmit = SubmitField('Save Changes')

class moveproduct(FlaskForm):
    mprodname = SelectField('Product Name')
    src = SelectField('Source')
    destination = SelectField('Destination')
    mprodqty = IntegerField('Quantity', validators=[DataRequired(), NumberRange(min=1, max=1000000)])
    staff = SelectField('Handled by', choices=[('', 'Unassigned')], default='')
    ts = DateTimeField(
        'Transfer date and time',
        format='%Y-%m-%dT%H:%M',
        default=lambda: datetime.datetime.now().replace(second=0, microsecond=0),
        validators=[DataRequired()]
    )
    movesubmit = SubmitField('Move')

class deletemovement(FlaskForm):
    submit = SubmitField('Delete')

class archiveform(FlaskForm):
    submit = SubmitField('Confirm')
