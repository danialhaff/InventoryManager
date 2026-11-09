from flask_wtf import FlaskForm
from wtforms import StringField,IntegerField,SelectField,SubmitField,DateTimeField
from wtforms.validators import DataRequired,Length,NumberRange,Optional
import datetime



class addproduct(FlaskForm):
    prodname = StringField('Product Name', validators=[DataRequired(), Length(max=20)])
    prodqty = IntegerField('Quantity', validators=[DataRequired(), NumberRange(min=1, max=1000000)])
    threshold = IntegerField('Low stock threshold', validators=[DataRequired(), NumberRange(min=0, max=1000000)])
    prodcategory = SelectField('Category', coerce=int, choices=[])
    prodsubmit = SubmitField('Save Changes')

class editproduct(FlaskForm):
    editname = StringField('Product Name', validators=[DataRequired(), Length(max=20)])
    threshold = IntegerField('Low stock threshold', validators=[DataRequired(), NumberRange(min=0, max=1000000)])
    editprodcategory = SelectField('Category', coerce=int, choices=[])
    editsubmit = SubmitField('Save Changes')

class addlocation(FlaskForm):
    locname = StringField('Location Name', validators=[DataRequired(), Length(max=20)])
    locsubmit = SubmitField('Save Changes')

class editlocation(FlaskForm):
    editlocname = StringField('Location Name', validators=[DataRequired(), Length(max=20)])
    editlocsubmit = SubmitField('Save Changes')

class addcategory(FlaskForm):
    catname = StringField('Category Name', validators=[DataRequired(), Length(max=30)])
    description = StringField('Description', validators=[Optional(), Length(max=100)])
    catsubmit = SubmitField('Save Changes')

class editcategory(FlaskForm):
    editcatname = StringField('Category Name', validators=[DataRequired(), Length(max=30)])
    editdescription = StringField('Description', validators=[Optional(), Length(max=100)])
    editcatsubmit = SubmitField('Save Changes')

class moveproduct(FlaskForm):
    mprodname = SelectField('Product Name')
    src = SelectField('Source')
    destination = SelectField('Destination')
    mprodqty = IntegerField('Quantity', validators=[DataRequired(), NumberRange(min=1, max=1000000)])
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
