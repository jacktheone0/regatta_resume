from flask_wtf import FlaskForm
from wtforms import (StringField, PasswordField, BooleanField, SelectField,
                     HiddenField, IntegerField, TextAreaField, DateField,
                     URLField)
from wtforms.validators import (DataRequired, Email, EqualTo, Length,
                                ValidationError, Optional, NumberRange, URL)
from models import User
from datetime import date


class LoginForm(FlaskForm):
    """Login form"""
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember_me = BooleanField('Remember Me')


class RegisterForm(FlaskForm):
    """Registration form"""
    full_name = StringField('Full Name', validators=[
        DataRequired(message='We need your name for your resume.'),
        Length(max=200)
    ])
    email = StringField('Email', validators=[
        DataRequired(),
        Email(),
        Length(max=120)
    ])
    password = PasswordField('Password', validators=[
        DataRequired(),
        Length(min=8, message='Password must be at least 8 characters')
    ])
    password_confirm = PasswordField('Confirm Password', validators=[
        DataRequired(),
        EqualTo('password', message='Passwords must match')
    ])
    role = SelectField('I am a:', choices=[
        ('sailor', 'Sailor'),
        ('coach', 'Coach')
    ], validators=[DataRequired()])

    def validate_email(self, field):
        """Check if email already exists"""
        if User.query.filter_by(email=field.data.lower()).first():
            raise ValidationError('This email is already registered.')


class AccountForm(FlaskForm):
    """Edit the name, email and school stored on the account."""
    full_name = StringField('Full Name', validators=[DataRequired(), Length(max=200)])
    display_name = StringField('Display Name (optional)', validators=[
        Optional(), Length(max=200)
    ])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=120)])
    grad_year = IntegerField('Graduation Year', validators=[
        Optional(), NumberRange(min=1900, max=date.today().year + 10)
    ])
    primary_school = StringField('School / Club', validators=[Optional(), Length(max=200)])

    def __init__(self, user=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def validate_email(self, field):
        """A changed address must not collide with another account."""
        existing = User.query.filter_by(email=field.data.lower()).first()
        if existing and (self.user is None or existing.id != self.user.id):
            raise ValidationError('That email is already in use.')


class ClaimProfileForm(FlaskForm):
    """
    Claim one scraped profile.

    Carries both target fields because a sailor can claim either a ClubSpot
    `Sailor` row or an HS/College `SailorName` row; exactly one is submitted.
    """
    sailor_id = HiddenField('Sailor ID', validators=[Optional()])
    sailor_name_id = HiddenField('Sailor Name ID', validators=[Optional()])
    evidence = TextAreaField('Anything that confirms this is you (optional)',
                             validators=[Optional(), Length(max=2000)])

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators=extra_validators):
            return False
        if bool(self.sailor_id.data) == bool(self.sailor_name_id.data):
            self.sailor_id.errors = list(self.sailor_id.errors) + [
                'Pick exactly one profile to claim.'
            ]
            return False
        return True


class ManualResultForm(FlaskForm):
    """A result the sailor adds themselves, for a regatta we missed."""
    regatta_name = StringField('Regatta Name', validators=[
        DataRequired(), Length(max=300)
    ])
    regatta_date = DateField('Date', validators=[Optional()])
    location = StringField('Location', validators=[Optional(), Length(max=200)])

    place_numeric = IntegerField('Your Place', validators=[
        Optional(), NumberRange(min=1, max=10000)
    ])
    total_boats = IntegerField('Boats in Fleet', validators=[
        Optional(), NumberRange(min=1, max=10000)
    ])
    boat_type = StringField('Boat / Fleet (e.g. 420, Laser)', validators=[
        Optional(), Length(max=100)
    ])
    division = StringField('Division', validators=[Optional(), Length(max=50)])
    role = SelectField('Role', choices=[
        ('', 'Not specified'),
        ('skipper', 'Skipper'),
        ('crew', 'Crew'),
    ], validators=[Optional()])
    team_name = StringField('Team / School', validators=[Optional(), Length(max=200)])
    crew_partner = StringField('Crew / Skipper Partner', validators=[
        Optional(), Length(max=200)
    ])
    source_url = URLField('Link to published results (optional)', validators=[
        Optional(), URL(message='Enter a full URL, including https://'), Length(max=500)
    ])
    notes = TextAreaField('Notes', validators=[Optional(), Length(max=2000)])

    def validate_total_boats(self, field):
        """A place larger than the fleet is a typo, not a result."""
        if field.data and self.place_numeric.data and self.place_numeric.data > field.data:
            raise ValidationError('Your place cannot be larger than the fleet size.')

    def validate_regatta_date(self, field):
        if field.data and field.data > date.today():
            raise ValidationError('That date is in the future.')
